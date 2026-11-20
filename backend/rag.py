"""RAG over NorthStar's SOP PDFs — answers with exact citations, or "I don't know".

Pipeline
  1. Load: read every docs/sops/*.pdf with pypdf (the PDFs are the source of truth).
  2. Chunk: split on section headers "SOP-XX §N  Heading" -> one chunk per section.
  3. Retrieve: BM25 over the chunks (no external services, deterministic, testable).
  4. Answer:
       - with ANTHROPIC_API_KEY: Claude writes the answer from the retrieved sections only,
         citing [SOP-XX §N] after each claim; citations are validated against what was retrieved.
       - without a key: extractive answer = the best matching section, quoted with its citation.
  5. Refuse: if retrieval is weak, or the model cites nothing valid -> "I don't know".
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from backend import llm

SOP_DIR = Path(__file__).resolve().parents[1] / "docs" / "sops"
IDK = "I don't know"

# Retrieval gate: below this, we refuse rather than guess.
MIN_SCORE = 2.6
MIN_TERM_HITS = 2

HEADER = re.compile(r"^(SOP-[A-Z]{2}) §(\d+)\s+(.+)$")
TITLE = re.compile(r"^(SOP-[A-Z]{2}): (.+)$")
TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("""a an and are as at be been but by can do does for from has have how i if in into is it its
me my of on or our should so than that the their them then there these they this to up us was we were
what when where which who why will with you your about after before any all also get got just need
please tell know does did am being""".split())
# A few business synonyms people actually type -> the words the SOPs use.
SYNONYMS = {
    "late": ["delayed", "late"], "delay": ["delayed"], "delays": ["delayed"],
    "money": ["refund", "credit"], "compensate": ["credit"], "compensation": ["credit"],
    "discount": ["credit"], "reimburse": ["refund"],
    "move": ["reassign"], "swap": ["reassign"], "transfer": ["reassign"], "reassigning": ["reassign"],
    "driver": ["technician"], "drivers": ["technician"], "tech": ["technician"],
    "message": ["update"], "text": ["sms", "update"], "notify": ["update", "contact"],
    "email": ["email", "update"], "call": ["contact", "phone"],
    "storm": ["weather", "lightning"], "rain": ["weather", "flooding"], "snow": ["ice", "weather"],
    "breakdown": ["breakdown", "vehicle"], "broke": ["breakdown"], "accident": ["collision", "incident"],
    "crash": ["collision", "incident"], "hurt": ["injury"], "injured": ["injury"],
    "times": ["times", "twice", "third"], "often": ["twice", "times"],
    "include": ["include"], "goes": ["include"], "contain": ["include"], "say": ["include", "template"],
}


def _stem(w: str) -> str:
    if len(w) <= 3:
        return w
    if w.endswith(("ied", "ies")) and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "shes", "ches", "xes", "zes")):
        return w[:-2]
    for suf in ("ments", "ment", "ings", "ing", "ed"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


DEFINITION_Q = re.compile(r"^(what|when) (is|are|does|do)\b|\bmean(s|ing)?\b|\bcount(s|ed)? as\b|\bdefin", re.I)


def tokenize(text: str, expand: bool = False) -> list[str]:
    out = []
    for w in TOKEN.findall(text.lower()):
        if w in STOP:
            continue
        words = SYNONYMS.get(w, [w]) if expand else [w]
        out.extend(_stem(x) for x in words)
    return out


@dataclass
class Chunk:
    sop_id: str
    section: str
    heading: str
    sop_title: str
    file: str
    text: str
    page: int = 1

    @property
    def cite(self) -> str:
        return f"{self.sop_id} §{self.section}"


@dataclass
class Citation:
    sop_id: str
    section: str
    heading: str
    sop_title: str
    file: str
    page: int
    quote: str

    @property
    def label(self) -> str:
        return f"{self.sop_id} §{self.section}"


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    mode: str = "extractive"          # 'claude' | 'extractive' | 'refused'
    retrieved: list[tuple[str, float]] = field(default_factory=list)

    @property
    def is_idk(self) -> bool:
        return self.text.strip() == IDK


# --------------------------------------------------------------------------- load + chunk
def load_chunks(sop_dir: Path = SOP_DIR) -> list[Chunk]:
    from pypdf import PdfReader

    chunks: list[Chunk] = []
    for pdf in sorted(sop_dir.glob("SOP-*.pdf")):
        reader = PdfReader(pdf)
        title, cur = "", None
        for page_no, page in enumerate(reader.pages, start=1):
            for raw in (page.extract_text() or "").splitlines():
                line = raw.strip()
                if not line:
                    continue
                if (m := TITLE.match(line)) and not title:
                    title = m.group(2)
                    continue
                if m := HEADER.match(line):
                    cur = Chunk(m.group(1), m.group(2), m.group(3).strip(), title, pdf.name, "", page_no)
                    chunks.append(cur)
                elif cur is not None:
                    cur.text = (cur.text + " " + line).strip()
    if not chunks:
        raise FileNotFoundError(f"No SOP PDFs found in {sop_dir} — run python docs/sops/build_sops.py")
    return chunks


# --------------------------------------------------------------------------- BM25
class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.75):
        self.chunks = chunks
        # Heading and SOP title count double: they say what a section is about.
        self.docs = [tokenize(f"{c.heading} {c.heading} {c.sop_title} {c.sop_title} {c.text}") for c in chunks]
        self.k1, self.b = k1, b
        self.avgdl = sum(map(len, self.docs)) / len(self.docs)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def search(self, query: str, k: int = 4) -> list[tuple[Chunk, float, int]]:
        q = set(tokenize(query, expand=True))
        if DEFINITION_Q.search(query):
            q.add(_stem("definitions"))
        results = []
        for i, tf in enumerate(self.tf):
            score, hits = 0.0, 0
            dl = len(self.docs[i])
            for t in q:
                if t in tf:
                    hits += 1
                    f = tf[t]
                    score += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if score > 0:
                results.append((self.chunks[i], score, hits))
        results.sort(key=lambda r: -r[1])
        return results[:k]


_index: BM25 | None = None


def index() -> BM25:
    global _index
    if _index is None:
        _index = BM25(load_chunks())
    return _index


def _citation(c: Chunk) -> Citation:
    return Citation(c.sop_id, c.section, c.heading, c.sop_title, c.file, c.page, c.text)


# --------------------------------------------------------------------------- answer
SYSTEM = f"""You are the NorthStar Logistics dispatch copilot. Answer ONLY from the SOP sections provided.
Rules:
- After every sentence, cite the section(s) it comes from in square brackets, exactly like [SOP-LD §2].
- Use only facts stated in the sections. Do not add outside knowledge or guess numbers.
- If the sections do not contain the answer, reply with exactly: {IDK}
- Be brief: 1-4 sentences."""

CITE_RE = re.compile(r"\[(SOP-[A-Z]{2}) §(\d+)\]")


def ask_sop(question: str, k: int = 4, client=None) -> Answer:
    hits = index().search(question, k=k)
    retrieved = [(c.cite, round(s, 2)) for c, s, _ in hits]
    if not hits or hits[0][1] < MIN_SCORE or hits[0][2] < min(MIN_TERM_HITS, len(set(tokenize(question)))):
        return Answer(IDK, [], "refused", retrieved)

    client = client if client is not None else llm.client()
    if not client:
        # Extractive: best section, plus a runner-up from the same SOP if it scores close.
        top, top_score, _ = hits[0]
        picked = [top] + [c for c, s, _ in hits[1:] if c.sop_id == top.sop_id and s >= 0.8 * top_score][:1]
        text = " ".join(f"{c.text} [{c.cite}]" for c in picked)
        return Answer(text, [_citation(c) for c in picked], "extractive", retrieved)

    context = "\n\n".join(f"[{c.cite}] {c.sop_title} — {c.heading}\n{c.text}" for c, _, _ in hits)
    text = llm.complete(client, SYSTEM, f"SOP sections:\n{context}\n\nQuestion: {question}").strip()
    if text == IDK or text.startswith(IDK):
        return Answer(IDK, [], "refused", retrieved)

    by_cite = {c.cite: c for c, _, _ in hits}
    cited = []
    for sop, sec in CITE_RE.findall(text):
        c = by_cite.get(f"{sop} §{sec}")
        if c and c not in cited:
            cited.append(c)
    # Guardrail: an answer with no citation to a retrieved section is not trusted.
    if not cited:
        return Answer(IDK, [], "refused", retrieved)
    # Drop any citation the model invented (points at a section we never gave it).
    text = CITE_RE.sub(lambda m: m.group(0) if f"{m.group(1)} §{m.group(2)}" in by_cite else "", text)
    return Answer(text, [_citation(c) for c in cited], "claude", retrieved)
