"""Static review dashboard for a completed batch."""

from __future__ import annotations

import csv
import html
import os
import statistics
from urllib.parse import quote
from pathlib import Path
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _link(target: str, root: Path, label: str) -> str:
    if not target:
        return '<span class="muted">-</span>'
    path = Path(target)
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        relative = str(path)
    return f'<a href="{html.escape(quote(relative))}">{html.escape(label)}</a>'


def write_review_html(output_root: str | Path, title: str = "RMC Assignment Marking - Review") -> Path:
    """Render gradebook.csv as a self-contained review page."""
    root = Path(output_root)
    gradebook = root / "gradebook.csv"
    rows: list[dict[str, Any]] = []
    if gradebook.exists():
        with gradebook.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

    totals = [_number(row.get("total_60")) for row in rows]
    mean = statistics.mean(totals) if totals else 0.0
    median = statistics.median(totals) if totals else 0.0
    finals = [row for row in rows if str(row.get("status", "")).lower() == "final"]
    flagged = [
        row
        for row in rows
        if str(row.get("needs_review", "")).lower() in {"true", "1", "yes"}
        and str(row.get("status", "")).lower() != "final"
    ]
    passing = [value for value in totals if value >= 24.0]

    rows.sort(
        key=lambda row: (
            str(row.get("needs_review", "")).lower() not in {"true", "1", "yes"},
            _number(row.get("total_60")),
        )
    )

    body = []
    for row in rows:
        total = _number(row.get("total_60"))
        needs_review = str(row.get("needs_review", "")).lower() in {"true", "1", "yes"}
        is_final = str(row.get("status", "")).lower() == "final"
        classes = []
        if is_final:
            classes.append("final")
        if needs_review and not is_final:
            classes.append("review")
        if total < 24:
            classes.append("fail")
        css = f' class="{" ".join(classes)}"' if classes else ""
        flag = "FINAL" if is_final else ("REVIEW" if needs_review else "DRAFT")
        body.append(
            f"<tr{css}>"
            f"<td>{html.escape(str(row.get('matric') or '-'))}</td>"
            f"<td><strong>{html.escape(str(row.get('name') or '-'))}</strong></td>"
            f"<td class=\"num\">{html.escape(str(row.get('clo2_org') or ''))}</td>"
            f"<td class=\"num\">{html.escape(str(row.get('clo2_align') or ''))}</td>"
            f"<td class=\"num\">{html.escape(str(row.get('clo2_lit') or ''))}</td>"
            f"<td class=\"num\">{html.escape(str(row.get('clo3_integrity') or ''))}</td>"
            f"<td class=\"num\">{html.escape(str(row.get('clo3_ethics') or ''))}</td>"
            f"<td class=\"num total\">{total:.2f}</td>"
            f"<td class=\"num\">{_number(row.get('percent')):.1f}%</td>"
            f"<td class=\"num\">{html.escape(str(row.get('comments') or ''))}</td>"
            f"<td class=\"flag\">{flag}</td>"
            f"<td>{_link(str(row.get('annotated') or ''), root, 'annotated')}</td>"
            f"<td>{_link(str(row.get('form') or ''), root, 'form')}</td>"
            "</tr>"
        )

    noun = "manuscript" if len(rows) == 1 else "manuscripts"
    statistics_block = (
        f"<p class=\"meta\">{len(rows)} {noun} | mean {mean:.2f}/60 | "
        f"median {median:.2f}/60 | at or above 40%: {len(passing)} | "
        f"confirmed final: {len(finals)} | awaiting review: {len(flagged)} | "
        f"draft: {len(rows) - len(finals) - len(flagged)}</p>"
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 32px; color: #1c2430; background: #fff; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; color: #1f3864; }}
  .meta {{ color: #5a6678; margin: 0 0 20px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border-bottom: 1px solid #e2e6ec; padding: 7px 8px; text-align: left; vertical-align: top; }}
  th {{ background: #f5f7fa; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; color: #445; position: sticky; top: 0; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.total {{ font-weight: 600; }}
  tr.review {{ background: #fff8e6; }}
  tr.final {{ background: #f1f8f1; }}
  tr.fail {{ background: #fdf0f0; }}
  td.flag {{ color: #9a6700; font-weight: 600; font-size: 12px; }}
  .muted {{ color: #9aa4b2; }}
  a {{ color: #1f5fbf; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .legend {{ margin-top: 14px; color: #5a6678; font-size: 12px; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
{statistics_block}
<table>
<thead>
<tr><th>Matric</th><th>Name</th><th class="num">Org</th><th class="num">Align</th><th class="num">Lit</th>
<th class="num">Integrity</th><th class="num">Ethics</th><th class="num">Total /60</th><th class="num">%</th>
<th class="num">Comments</th><th>Flag</th><th>Annotated</th><th>Form</th></tr>
</thead>
<tbody>
{''.join(body) if body else '<tr><td colspan="13" class="muted">No manuscripts in the gradebook yet.</td></tr>'}
</tbody>
</table>
<p class="legend">Highlighted rows need attention: yellow = flagged for human review, red = below the 40% passing mark.
Each student folder contains the annotated manuscript, the filled marking form, and the machine-readable result.</p>
</body>
</html>
"""
    target = root / "review.html"
    target.write_text(page, encoding="utf-8")
    return target
