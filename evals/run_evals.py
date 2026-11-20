"""Run the 30-question eval suite and report accuracy, citation rate, latency and cost.

  python -m evals.run_evals --label baseline
  python -m evals.run_evals --label after-fixes
  python -m evals.run_evals --label holdout --questions questions_holdout.json   # never tuned on

Data: a deterministic dataset (as of 2026-11-19 14:00) loaded into its own database
(`dispatch_eval`), so results are reproducible and never touch your working data.

Grading (deliberately strict, and independent of the code under test):
  sop   correct = the expected SOP section is cited AND every required phrase is in the answer
                (exactly, or all of its words in any order — so a paraphrase from Claude still counts)
  idk   correct = the copilot answers exactly "I don't know"
  jobs  correct = the set of job IDs in the answer equals the gold set, computed here with
                  pandas straight from the tables (not via backend/queries.py)
Metrics:
  accuracy        correct / total (also per type)
  citation rate   answered questions (not "I don't know") whose answer cites >= 1 SOP section
  citation hit    SOP questions whose expected section is among the citations
  latency         wall-clock per question (p50 / p95)
  cost            Claude tokens x PRICE_*_PER_MTOK (USD 0 in offline mode: no model calls)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from backend import copilot, db, llm
from backend.auth import get_user
from backend.demo_data import load_into

ROOT = Path(__file__).resolve().parent
AS_OF = datetime(2026, 11, 19, 14, 0)
EVAL_DB = os.getenv("EVAL_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/dispatch_eval")
JOB_ID = re.compile(r"\bJ-\d{4}\b")


def gold_jobs(conn, spec: dict) -> tuple[set[str], str]:
    """Independent re-implementation of 'late jobs' with pandas. Returns (job ids, username to ask as)."""
    def frame(sql):
        with conn.cursor() as cur:
            cur.execute(sql)
            return pd.DataFrame([dict(r) for r in cur.fetchall()])
    jobs = frame("SELECT * FROM jobs")
    for col in ("scheduled_at", "window_end_at", "sla_due_at", "completed_at"):
        jobs[col] = pd.to_datetime(jobs[col])
    cust = frame("SELECT customer_id, tier FROM customers")
    df = jobs.merge(cust, on="customer_id")
    day = (AS_OF + timedelta(days=spec.get("day_offset", 0))).date()
    df = df[df["scheduled_at"].dt.date == day]
    finish = df["completed_at"].fillna(AS_OF)
    started = (df["scheduled_at"] <= AS_OF) & (df["status"] != "Cancelled")
    df = df.assign(delayed=started & (finish > df["window_end_at"]), breach=started & (finish > df["sla_due_at"]))
    user = "manager"
    if spec.get("as") == "busiest_late_technician":
        tech = df[df["delayed"]].groupby("technician_id").size().sort_values(ascending=False).index[0]
        df, user = df[df["technician_id"] == tech], tech.lower()
    if "city" in spec:
        df = df[df["city"] == spec["city"]]
    if "tier" in spec:
        df = df[df["tier"] == spec["tier"]]
    hit = df["breach"] if spec.get("breach") else df["delayed"]
    return set(df.loc[hit, "job_id"]), user


WORD = re.compile(r"[a-z0-9%]+")


def has_phrase(phrase: str, text: str) -> bool:
    """Exact phrase, or every word of it present in any order (models paraphrase: 'Gold 2 hours' ~ '2 hours for Gold')."""
    low = text.lower()
    return phrase.lower() in low or set(WORD.findall(phrase.lower())) <= set(WORD.findall(low))


def grade(item: dict, ans, gold: set[str] | None) -> dict:
    labels = [c.label for c in ans.citations]
    text = ans.text or ""
    out = {"kind": ans.kind, "citations": labels, "cited": bool(labels)}
    if item["type"] == "idk":
        out["correct"] = ans.kind == "idk"
        out["why"] = "" if out["correct"] else f"answered instead of I don't know: {text[:100]!r}"
    elif item["type"] == "sop":
        missing = [m for m in item["must"] if not has_phrase(m, text)]
        out["cite_hit"] = item["cite"] in labels
        out["correct"] = out["cite_hit"] and not missing and ans.kind == "sop"
        out["why"] = "" if out["correct"] else (
            f"routed to {ans.kind}" if ans.kind != "sop" else
            f"cited {labels or 'nothing'} (want {item['cite']})" if not out["cite_hit"] else f"missing {missing}")
    else:
        got = set(JOB_ID.findall(text))
        out["correct"] = ans.kind == "jobs" and got == gold
        out["why"] = "" if out["correct"] else (
            f"routed to {ans.kind}" if ans.kind != "jobs" else
            f"extra {sorted(got - gold)[:6]} missing {sorted(gold - got)[:6]} (got {len(got)}, want {len(gold)})")
        out["gold"] = sorted(gold)
    return out


def run(label: str, reload: bool = True, questions: str = "questions.json") -> dict:
    if reload:
        load_into(EVAL_DB, AS_OF, recreate_db=True)
    items = json.loads((ROOT / questions).read_text())
    rows = []
    llm.reset_usage()
    client = llm.client()
    with db.connect(EVAL_DB) as conn:
        for item in items:
            gold, username = (gold_jobs(conn, item["spec"]) if item["type"] == "jobs" else (None, "manager"))
            user = get_user(conn, username)
            before = dict(llm.USAGE)
            t0 = time.perf_counter()
            ans = copilot.ask(conn, user, item["q"], AS_OF, client=client or False)
            ms = (time.perf_counter() - t0) * 1000
            used = {k: llm.USAGE[k] - before[k] for k in llm.USAGE}
            rows.append({"id": item["id"], "type": item["type"], "q": item["q"], "as": username,
                         "latency_ms": round(ms, 1), "cost_usd": llm.cost_usd(used), **used,
                         **grade(item, ans, gold), "answer": ans.text[:600]})

    answered = [r for r in rows if r["kind"] != "idk"]
    lat = [r["latency_ms"] for r in rows]
    summary = {
        "label": label, "ran_at": datetime.now().isoformat(timespec="seconds"),
        "mode": f"claude ({llm.MODEL})" if client else "offline (extractive answers, no model calls)",
        "n": len(rows),
        "accuracy": sum(r["correct"] for r in rows) / len(rows),
        "accuracy_by_type": {t: sum(r["correct"] for r in rows if r["type"] == t) / sum(r["type"] == t for r in rows)
                             for t in ("sop", "idk", "jobs")},
        "citation_rate": sum(r["cited"] for r in answered) / len(answered) if answered else 0.0,
        "citation_hit_sop": sum(r.get("cite_hit", False) for r in rows if r["type"] == "sop")
                            / sum(r["type"] == "sop" for r in rows),
        "latency_ms_p50": statistics.median(lat),
        "latency_ms_p95": sorted(lat)[int(0.95 * (len(lat) - 1))],
        "llm_calls": llm.USAGE["calls"], "input_tokens": llm.USAGE["input_tokens"],
        "output_tokens": llm.USAGE["output_tokens"], "cost_usd": round(llm.cost_usd(), 4),
        "failures": [{"id": r["id"], "q": r["q"], "why": r["why"]} for r in rows if not r["correct"]],
    }
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{label}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str))
    return summary


def markdown_summary(s: dict) -> str:
    a = s["accuracy_by_type"]
    lines = [f"### {s['label']} · {s['mode']}", "",
             "| Qs | Accuracy | SOP | I don't know | Jobs | Citation rate | SOP citation hit | p50 / p95 latency | Model calls | Tokens in / out | Cost |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
             f"| {s['n']} | **{s['accuracy']:.0%}** | {a['sop']:.0%} | {a['idk']:.0%} | {a['jobs']:.0%} | "
             f"{s['citation_rate']:.0%} | {s['citation_hit_sop']:.0%} | {s['latency_ms_p50']:.0f} / {s['latency_ms_p95']:.0f} ms | "
             f"{s['llm_calls']} | {s['input_tokens']:,} / {s['output_tokens']:,} | ${s['cost_usd']:.4f} |", ""]
    if s["failures"]:
        lines += ["Failures:", ""] + [f"- `{f['id']}` {f['q']} → {f['why']}" for f in s["failures"]] + [""]
    return "\n".join(lines)


def print_summary(s: dict) -> None:
    print(f"\n== {s['label']} · {s['mode']} ==")
    print(f"accuracy        {s['accuracy']:.0%}  (sop {s['accuracy_by_type']['sop']:.0%} · "
          f"idk {s['accuracy_by_type']['idk']:.0%} · jobs {s['accuracy_by_type']['jobs']:.0%})")
    print(f"citation rate   {s['citation_rate']:.0%}   SOP citation hit {s['citation_hit_sop']:.0%}")
    print(f"latency         p50 {s['latency_ms_p50']:.0f} ms · p95 {s['latency_ms_p95']:.0f} ms")
    print(f"cost            ${s['cost_usd']:.4f}  ({s['llm_calls']} model calls)")
    for f in s["failures"]:
        print(f"  ✗ {f['id']:7} {f['q'][:55]:55} → {f['why']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--no-reload", action="store_true")
    ap.add_argument("--questions", default="questions.json", help="questions.json or questions_holdout.json")
    a = ap.parse_args()
    summary = run(a.label, reload=not a.no_reload, questions=a.questions)
    print_summary(summary)
    if os.getenv("GITHUB_STEP_SUMMARY"):            # shows as a table on the GitHub Actions run page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(markdown_summary(summary) + "\n")
