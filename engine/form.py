"""Fill the official marking form and append a marking-summary page."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .rubric import ASSIGNMENT_MAX, CRITERIA, band_for, compute

_NAVY = colors.HexColor("#1F3864")
_GOLD = colors.HexColor("#BF9000")
_GREY = colors.HexColor("#666666")
_LIGHT = colors.HexColor("#F2F2F2")

_FONT_REG = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
_FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
_FONT_UNICODE = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
_fonts_ready = False


def _register_fonts() -> tuple[str, str, str]:
    """Register Arial once; fall back to reportlab's built-in Helvetica."""
    global _fonts_ready
    if not _fonts_ready:
        if _FONT_REG.exists() and _FONT_BOLD.exists():
            pdfmetrics.registerFont(TTFont("RmcArial", str(_FONT_REG)))
            pdfmetrics.registerFont(TTFont("RmcArial-Bold", str(_FONT_BOLD)))
        if _FONT_UNICODE.exists():
            pdfmetrics.registerFont(TTFont("RmcArialUni", str(_FONT_UNICODE)))
        _fonts_ready = True
    regular = "RmcArial" if _FONT_REG.exists() and _FONT_BOLD.exists() else "Helvetica"
    bold = "RmcArial-Bold" if _FONT_REG.exists() and _FONT_BOLD.exists() else "Helvetica-Bold"
    unicode_font = "RmcArialUni" if _FONT_UNICODE.exists() else regular
    return regular, bold, unicode_font


def _needs_unicode(text: str) -> bool:
    return any(ord(ch) > 0x2FF for ch in text)


def _fit_textbox(
    page: pymupdf.Page,
    rect: tuple[float, float, float, float],
    text: str,
    font_alias: str,
    font_file: str | None,
    align: int = 0,
) -> None:
    """Write text into a fixed box, shrinking the font until it fits."""
    box = pymupdf.Rect(rect[0] + 2, rect[1] + 1, rect[2] - 1, rect[3] - 1)
    value = text.strip()
    for size in (11.0, 10.0, 9.0, 8.0, 7.0):
        try:
            result = page.insert_textbox(
                box,
                value,
                fontname=font_alias,
                fontfile=font_file,
                fontsize=size,
                color=(0, 0, 0),
                align=align,
            )
        except (ValueError, RuntimeError):
            continue
        if result >= 0:
            return
    cutoff = max(1, int(len(value) * 0.9))
    page.insert_textbox(
        box,
        value[:cutoff] + "…",
        fontname=font_alias,
        fontfile=font_file,
        fontsize=7.0,
        color=(0, 0, 0),
        align=align,
    )


# Layout coordinates measured from the official marking form and its filled
# sample. The form is a fixed template, so these are stable.
_FIELD_RECTS = {
    "student_name": (160.0, 65.0, 480.0, 81.0),
    "matric_number": (160.0, 82.0, 480.0, 98.0),
    "evaluator_name": (160.0, 99.0, 480.0, 115.0),
}
_MARK_RECTS = {
    "clo2_org": (706.0, 296.0, 748.0, 312.0),
    "clo2_align": (706.0, 400.0, 748.0, 416.0),
    "clo2_lit": (706.0, 536.0, 748.0, 552.0),
    "clo3_integrity": (706.0, 730.0, 748.0, 746.0),
    "clo3_ethics": (706.0, 829.0, 748.0, 845.0),
}


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold, _ = _register_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RmcTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=13.5,
            leading=17,
            textColor=colors.black,
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "RmcMeta",
            parent=base["Normal"],
            fontName=regular,
            fontSize=9.5,
            leading=13,
            textColor=colors.black,
        ),
        "h2": ParagraphStyle(
            "RmcH2",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=12,
            leading=15,
            textColor=_NAVY,
            spaceBefore=12,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "RmcBody",
            parent=base["Normal"],
            fontName=regular,
            fontSize=9.5,
            leading=12.5,
            textColor=colors.black,
        ),
        "bullet": ParagraphStyle(
            "RmcBullet",
            parent=base["Normal"],
            fontName=regular,
            fontSize=9.2,
            leading=12.2,
            leftIndent=11,
            bulletIndent=2,
            textColor=colors.black,
        ),
        "note": ParagraphStyle(
            "RmcNote",
            parent=base["Normal"],
            fontName=regular,
            fontSize=8,
            leading=10.5,
            textColor=_GREY,
        ),
    }


def _table(data, widths, header: bool = True) -> Table:
    table = Table(data, colWidths=widths, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#8C8C8C")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        commands += [
            ("BACKGROUND", (0, 0), (-1, 0), _LIGHT),
            ("FONTNAME", (0, 0), (-1, 0), _register_fonts()[1]),
            ("ALIGN", (2, 0), (-1, 0), "CENTER"),
        ]
    commands.append(("FONTNAME", (0, 1), (-1, -1), _register_fonts()[0]))
    table.setStyle(TableStyle(commands))
    return table


def _summary_pdf(
    path: Path,
    meta: dict[str, str],
    result: dict[str, object],
    computed: dict[str, object],
    evaluator: str,
    semester: str,
    confirmation: dict[str, object] | None = None,
) -> None:
    """Write the one-page marking summary with scores, strengths, and fixes."""
    regular, bold, _ = _register_fonts()
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=14 * mm,
        title="PPS10203 Assignment Marking Summary",
        author=evaluator,
    )
    story: list[object] = []
    story.append(Paragraph("PPS10203 Research Methodology - Assignment Marking Summary", styles["title"]))
    name = meta.get("name") or "Unknown candidate"
    matric = meta.get("matric") or "-"
    title = meta.get("title") or "-"
    story.append(
        Paragraph(
            f"<b>Candidate:</b> {escape(name)} ({escape(matric)}) | "
            f"<b>Title:</b> {escape(title)}",
            styles["meta"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Evaluator:</b> {escape(evaluator)} | {escape(semester)}",
            styles["meta"],
        )
    )
    story.append(Spacer(1, 7))

    criteria = result.get("criteria") or {}
    rows = [[Paragraph("<b>CLO</b>", styles["body"]), Paragraph("<b>Criterion</b>", styles["body"]),
             Paragraph("<b>Score</b>", styles["body"]), Paragraph("<b>Band</b>", styles["body"])]]
    for criterion in CRITERIA:
        score = int(criteria[criterion.id]["score"])  # type: ignore[index]
        rows.append(
            [
                Paragraph(criterion.clo, styles["body"]),
                Paragraph(escape(criterion.name), styles["body"]),
                Paragraph(f"{score} / 10", styles["body"]),
                Paragraph(band_for(score), styles["body"]),
            ]
        )
    story.append(_table(rows, [35, 285, 45, 70]))

    story.append(Spacer(1, 8))
    story.append(
        _table(
            [
                [Paragraph("<b>Component</b>", styles["body"]), Paragraph("<b>Raw</b>", styles["body"]),
                 Paragraph("<b>Weight</b>", styles["body"]), Paragraph("<b>Weighted mark</b>", styles["body"])],
                [
                    Paragraph("CLO 2 (three criteria)", styles["body"]),
                    Paragraph(f"{computed['clo2_raw']} / 30", styles["body"]),
                    Paragraph("40%", styles["body"]),
                    Paragraph(f"{computed['clo2_weighted']:.2f} / 40", styles["body"]),
                ],
                [
                    Paragraph("CLO 3 (two criteria)", styles["body"]),
                    Paragraph(f"{computed['clo3_raw']} / 20", styles["body"]),
                    Paragraph("20%", styles["body"]),
                    Paragraph(f"{computed['clo3_weighted']:.2f} / 20", styles["body"]),
                ],
                [
                    Paragraph("Assignment total", styles["body"]),
                    Paragraph("-", styles["body"]),
                    Paragraph("60%", styles["body"]),
                    Paragraph(
                        f"<b>{computed['total']:.2f} / 60 ({computed['percent']:.1f}%)</b>",
                        styles["body"],
                    ),
                ],
            ],
            [135, 75, 65, 115],
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "CLO 2 = subtotal / 30 x 40; CLO 3 = subtotal / 20 x 20. "
            "The assignment contributes 60 percentage points; the final examination contributes 40.",
            styles["note"],
        )
    )

    story.append(Paragraph("Strengths", styles["h2"]))
    strengths = result.get("strengths") or []
    for item in strengths:
        story.append(Paragraph(f"• {escape(str(item))}", styles["bullet"]))

    story.append(Paragraph("Priority improvements", styles["h2"]))
    improvements = result.get("improvements") or []
    for item in improvements:
        story.append(Paragraph(f"• {escape(str(item))}", styles["bullet"]))

    story.append(Spacer(1, 10))
    if confirmation:
        confirmed_by = escape(str(confirmation.get("confirmed_by") or evaluator))
        confirmed_at = str(confirmation.get("confirmed_at") or "")
        try:
            confirmed_date = datetime.fromisoformat(confirmed_at).strftime("%d %B %Y")
        except ValueError:
            confirmed_date = confirmed_at
        footer = (
            f"Confirmed as final by {confirmed_by} on {confirmed_date}. "
            "Marginal comments reference the relevant criterion (CLO 2 or CLO 3)."
        )
    else:
        footer = (
            "Marginal comments reference the relevant criterion (CLO 2 or CLO 3). "
            "This marking sheet is prepared for review by the course coordinator; it is "
            "not an official record until confirmed."
        )
    story.append(KeepTogether(Paragraph(footer, styles["note"])))
    doc.build(story)


def fill_form(
    template: str | Path,
    destination: str | Path,
    meta: dict[str, str],
    result: dict[str, object],
    evaluator: str,
    semester: str,
    confirmation: dict[str, object] | None = None,
) -> dict[str, object]:
    """Fill page 1 of the form and append the computed summary page."""
    _register_fonts()
    scores = {cid: int(result["criteria"][cid]["score"]) for cid in _MARK_RECTS}  # type: ignore[index]
    computed = compute(scores)

    form_doc = pymupdf.open(template)
    page = form_doc[0]
    use_unicode = _needs_unicode(meta.get("name", "") + meta.get("matric", ""))
    if use_unicode and _FONT_UNICODE.exists():
        font_file: str | None = str(_FONT_UNICODE)
        alias = "rmcuni"
    elif _FONT_REG.exists():
        font_file = str(_FONT_REG)
        alias = "rmcform"
    else:
        font_file = None
        alias = "helv"
    for field, rect in _FIELD_RECTS.items():
        _fit_textbox(page, rect, meta.get(field, "") or "", alias, font_file)
    for criterion_id, rect in _MARK_RECTS.items():
        _fit_textbox(page, rect, f"{scores[criterion_id]} / 10", alias, font_file)

    summary_meta = {
        "name": meta.get("student_name", ""),
        "matric": meta.get("matric_number", ""),
        "title": meta.get("title", ""),
    }
    with tempfile.TemporaryDirectory(prefix="rmc-summary-") as tmp:
        summary_path = Path(tmp) / "summary.pdf"
        _summary_pdf(
            summary_path, summary_meta, result, computed, evaluator, semester, confirmation
        )
        summary_doc = pymupdf.open(summary_path)
        form_doc.insert_pdf(summary_doc)
        summary_doc.close()

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    form_doc.save(destination, garbage=3, deflate=True)
    form_doc.close()
    return computed
