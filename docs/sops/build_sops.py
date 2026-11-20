"""Render docs/sops/sops.json into five 1-page SOP PDFs (docs/sops/*.pdf).

Run: python docs/sops/build_sops.py
Each section is printed as "<SOP-ID> §<n>  <heading>" so the RAG can cite it exactly.
"""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent


def build_one(sop: dict, out: Path) -> None:
    ss = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=ss["Title"], fontSize=18, spaceAfter=4, alignment=0)
    head = ParagraphStyle("h", parent=ss["Heading3"], fontSize=11.5, spaceBefore=8, spaceAfter=2)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=11, leading=14.5)

    story = [
        Paragraph(f"{sop['id']}: {sop['title']}", title),
        Table([["NorthStar Logistics", f"Owner: {sop['owner']}", f"Effective: {sop['effective']}"]],
              colWidths=[2.2 * inch, 2.6 * inch, 2.1 * inch],
              style=TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#555555")),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.HexColor("#999999")),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0)])),
        Spacer(1, 6),
    ]
    for num, heading, text in sop["sections"]:
        story.append(Paragraph(f"{sop['id']} §{num}  {heading}", head))
        story.append(Paragraph(text, body))
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, title=f"{sop['id']} {sop['title']}",
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch)
    doc.build(story)


def main() -> None:
    from pypdf import PdfReader
    sops = json.loads((HERE / "sops.json").read_text())
    for sop in sops:
        out = HERE / sop["file"]
        build_one(sop, out)
        pages = len(PdfReader(out).pages)
        assert pages == 1, f"{out.name} is {pages} pages; SOPs must be 1 page"
        print(f"wrote {out.name} (1 page)")


if __name__ == "__main__":
    main()
