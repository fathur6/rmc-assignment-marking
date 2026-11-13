"""Page-accurate text extraction and anchor location.

The marker model quotes short passages from the manuscript. Those quotes must
map back to character boxes so the annotation can be drawn exactly on the
source text, even when the PDF stores broken runs (for example a year split
into ``2`` ``0`` ``1`` ``4`` with variation selectors).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Zero-width and variation selectors that Word/PDF producers interleave with
# digits and ligatures; they are invisible but break literal matching.
_IGNORED = {
    "\ufe0f",
    "\ufe0e",
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
    "\u00ad",
    "\u200e",
    "\u200f",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}

_WS = re.compile(r"\s+")


def _clean_char(raw: str) -> str:
    """Normalize one extracted character; return '' when it is invisible."""
    if raw in _IGNORED:
        return ""
    ch = unicodedata.normalize("NFC", raw)
    if ch.isspace():
        return " "
    if unicodedata.category(ch) in {"Cc", "Cf"}:
        return ""
    return ch


def normalize_text(value: str) -> str:
    """Normalize a quote or passage for whitespace-insensitive comparison."""
    out = []
    for raw in value:
        ch = _clean_char(raw)
        if not ch:
            continue
        if ch == " " and (not out or out[-1] == " "):
            continue
        out.append(ch)
    return "".join(out).strip()


@dataclass(frozen=True)
class Char:
    """One normalized source character and its page box."""

    text: str
    bbox: tuple[float, float, float, float] | None
    line: int


@dataclass(frozen=True)
class Match:
    """A located quote: character span in PageText.text plus a match quality."""

    start: int
    end: int
    score: float

    @property
    def text(self) -> str:  # pragma: no cover - convenience accessor
        return ""


@dataclass
class PageText:
    """Extracted text for one page, index-aligned with Char boxes."""

    number: int
    width: float
    height: float
    text: str
    chars: list[Char]

    def find(self, anchor: str) -> Match | None:
        """Locate a quote, tolerating whitespace, case, and punctuation drift."""
        query = normalize_text(anchor)
        if not query:
            return None
        words = query.split()
        if not words:
            return None

        pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
        found = pattern.search(self.text)
        if found:
            return Match(found.start(), found.end(), 1.0)

        punct_words = re.findall(r"[^\W_]+(?:['’][^\W_]+)?", query, re.UNICODE)
        if len(punct_words) >= 3:
            loose = re.compile(
                r"[^\w]+".join(re.escape(word) for word in punct_words), re.IGNORECASE
            )
            found = loose.search(self.text)
            if found:
                return Match(found.start(), found.end(), 0.9)

        matcher = difflib.SequenceMatcher(None, query, self.text, autojunk=False)
        block = matcher.find_longest_match(0, len(query), 0, len(self.text))
        threshold = max(18, int(len(query) * 0.7))
        if block.size >= threshold:
            return Match(block.b, block.b + block.size, block.size / max(1, len(query)))
        return None

    def highlight_rects(self, match: Match) -> list[tuple[float, float, float, float]]:
        """Union character boxes per text line into highlight rectangles."""
        by_line: dict[int, list[tuple[float, float, float, float]]] = {}
        for char in self.chars[match.start : match.end]:
            if char.bbox is None or char.text.isspace():
                continue
            by_line.setdefault(char.line, []).append(char.bbox)
        rects: list[tuple[float, float, float, float]] = []
        for boxes in by_line.values():
            x0 = min(b[0] for b in boxes)
            y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y1 = max(b[3] for b in boxes)
            rects.append((x0 - 1.0, y0 - 1.0, x1 + 1.0, y1 + 1.0))
        rects.sort(key=lambda r: (r[1], r[0]))
        merged: list[list[float]] = []
        for rect in rects:
            if merged and abs(rect[1] - merged[-1][3]) <= 2.0:
                merged[-1][2] = max(merged[-1][2], rect[2])
                merged[-1][3] = max(merged[-1][3], rect[3])
            else:
                merged.append([rect[0], rect[1], rect[2], rect[3]])
        return [tuple(r) for r in merged]  # type: ignore[misc]


def extract_pages(source: str | Path) -> list[PageText]:
    """Extract every page as normalized text plus character boxes."""
    pages: list[PageText] = []
    with pymupdf.open(source) as doc:
        for index, page in enumerate(doc):
            chars: list[Char] = []
            pieces: list[str] = []
            line_no = -1
            raw = page.get_text("rawdict")
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    line_no += 1
                    if line_no > 0:
                        chars.append(Char("\n", None, line_no))
                        pieces.append("\n")
                    for span in line.get("spans", []):
                        for item in span.get("chars", []):
                            ch = _clean_char(item.get("c", ""))
                            if not ch:
                                continue
                            if ch == " " and pieces and pieces[-1] in {" ", "\n"}:
                                continue
                            bbox = tuple(round(v, 2) for v in item.get("bbox", (0, 0, 0, 0)))
                            chars.append(Char(ch, bbox, line_no))
                            pieces.append(ch)
            pages.append(
                PageText(
                    number=index + 1,
                    width=page.rect.width,
                    height=page.rect.height,
                    text="".join(pieces),
                    chars=chars,
                )
            )
    return pages


def manuscript_text(pages: list[PageText]) -> str:
    """Render pages for the prompt with page boundaries the model can cite."""
    blocks = []
    for page in pages:
        body = page.text.strip("\n")
        blocks.append(f'<page n="{page.number}">\n{body}\n</page>')
    return "\n\n".join(blocks)


def resolve_anchor(
    pages: list[PageText], page_number: int, anchor: str
) -> tuple[int, Match, list[tuple[float, float, float, float]]] | None:
    """Find a quote, correcting a wrong page number when the quote is unique.

    Returns ``(page_number, match, rects)`` or None.
    """
    if not 1 <= page_number <= len(pages):
        page_number = 1
    page = pages[page_number - 1]
    match = page.find(anchor)
    if match:
        rects = page.highlight_rects(match)
        if rects:
            return page_number, match, rects
    for candidate in pages:
        if candidate.number == page_number:
            continue
        match = candidate.find(anchor)
        if match:
            rects = candidate.highlight_rects(match)
            if rects:
                return candidate.number, match, rects
    return None
