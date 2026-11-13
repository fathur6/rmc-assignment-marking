"""Draw criterion-tagged margin comments onto the student's manuscript."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .extract import PageText, extract_pages, resolve_anchor
from .rubric import Criterion

_RED = (1.0, 0.0, 0.0)


@dataclass
class AnnotationStats:
    """Outcome of annotating one manuscript."""

    placed: int = 0
    unmatched: list[dict[str, object]] = field(default_factory=list)
    corrected_pages: list[dict[str, object]] = field(default_factory=list)
    notes_per_page: dict[int, int] = field(default_factory=dict)


def comment_text(clo: str, category: str, comment: str) -> str:
    """Canonical margin-note text, e.g. ``[CLO2 - Title] ...``."""
    return f"[{clo} - {category}] {comment.strip()}"


def _next_free_y(used: dict[float, int], y: float, height: float) -> float:
    """Nudge a sticky note down until it does not overlap an earlier note."""
    candidate = max(56.0, min(y, height - 60.0))
    for _ in range(40):
        if all(abs(candidate - other) >= 20.0 for other in used):
            used[candidate] = 1
            return candidate
        candidate += 21.0
        if candidate > height - 60.0:
            candidate = 56.0
    used[candidate] = 1
    return candidate


def annotate_pdf(
    source: str | Path,
    destination: str | Path,
    comments: list[dict[str, object]],
    evaluator: str,
    pages: list[PageText] | None = None,
) -> AnnotationStats:
    """Write a copy of ``source`` with red highlights and margin notes."""
    pages = pages or extract_pages(source)
    stats = AnnotationStats()
    doc = pymupdf.open(source)
    used_y: dict[int, dict[float, int]] = {}

    ordered = sorted(
        comments,
        key=lambda c: (int(c.get("page", 1)), int(c.get("order", 0))),
    )
    for item in ordered:
        page_number = int(item.get("page", 1) or 1)
        anchor = str(item.get("anchor", "") or "")
        resolved = resolve_anchor(pages, page_number, anchor) if anchor else None
        if resolved is None:
            stats.unmatched.append(
                {
                    "page": page_number,
                    "anchor": anchor,
                    "comment": item.get("comment", ""),
                }
            )
            page_number = min(max(page_number, 1), len(pages))
            y = 72.0
            rects: list[tuple[float, float, float, float]] = []
        else:
            resolved_page, _match, rects = resolved
            if resolved_page != page_number:
                stats.corrected_pages.append(
                    {"from": page_number, "to": resolved_page, "anchor": anchor}
                )
                page_number = resolved_page
            y = rects[0][1] if rects else 72.0

        page = doc[page_number - 1]
        for rect in rects:
            highlight = page.add_highlight_annot(pymupdf.Rect(rect))
            highlight.set_colors(stroke=_RED)
            highlight.set_opacity(0.35)
            highlight.update()

        used = used_y.setdefault(page_number, {})
        note_y = _next_free_y(used, y, page.rect.height)
        note_x = max(page.rect.width - 39.0, page.rect.width * 0.88)
        note = page.add_text_annot(pymupdf.Point(note_x, note_y), "")
        note.set_info(
            title=evaluator,
            content=comment_text(
                str(item.get("clo", "CLO2")),
                str(item.get("category", "Feedback")),
                str(item.get("comment", "")),
            ),
        )
        note.set_colors(stroke=_RED)
        note.update()
        stats.placed += 1
        stats.notes_per_page[page_number] = stats.notes_per_page.get(page_number, 0) + 1

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc.save(destination, garbage=3, deflate=True)
    doc.close()
    return stats
