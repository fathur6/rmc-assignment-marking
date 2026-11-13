"""One-manuscript orchestration: grade, annotate, fill, record."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .annotate import annotate_pdf
from .config import Config
from .extract import extract_pages
from .form import fill_form
from .grade import grade
from .rubric import CLO2_MAX_RAW, CLO3_MAX_RAW

FIELDS = [
    "matric",
    "name",
    "title",
    "clo2_org",
    "clo2_align",
    "clo2_lit",
    "clo3_integrity",
    "clo3_ethics",
    "clo2_raw",
    "clo3_raw",
    "total_60",
    "percent",
    "confidence",
    "needs_review",
    "review_reasons",
    "comments",
    "annotated",
    "form",
    "result_json",
    "model",
    "generated_at",
    "source_pdf",
    "source_sha256",
    "status",
    "confirmed_at",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str, fallback: str = "student") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value).strip(" .-")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:110]


def folder_for(config: Config, matric: str, name: str, fallback: str) -> str:
    pattern = config.batch.folder_pattern
    try:
        rendered = pattern.format(matric=matric or "", name=name or "")
    except (KeyError, IndexError):
        rendered = f"{matric} - {name}"
    rendered = re.sub(r"\s*-\s*$", "", rendered.strip())
    rendered = rendered.strip(" -")
    if not rendered:
        rendered = fallback
    return safe_name(rendered)


def build_index(output_root: str | Path) -> dict[str, Path]:
    """Map each already-marked source hash to its result.json, for fast resume."""
    index: dict[str, Path] = {}
    root = Path(output_root)
    if not root.exists():
        return index
    for path in root.glob("*/result.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        digest = str(data.get("source", {}).get("sha256") or "")
        if digest:
            index[digest] = path
    return index


def row_from_payload(payload: dict[str, Any], artifacts: dict[str, str]) -> dict[str, Any]:
    criteria = payload["criteria"]
    computed = payload["computed"]
    meta = payload["meta"]
    return {
        "matric": meta["matric"],
        "name": meta["name"],
        "title": meta["title"],
        "clo2_org": criteria["clo2_org"]["score"],
        "clo2_align": criteria["clo2_align"]["score"],
        "clo2_lit": criteria["clo2_lit"]["score"],
        "clo3_integrity": criteria["clo3_integrity"]["score"],
        "clo3_ethics": criteria["clo3_ethics"]["score"],
        "clo2_raw": f"{computed['clo2_raw']}/{int(CLO2_MAX_RAW)}",
        "clo3_raw": f"{computed['clo3_raw']}/{int(CLO3_MAX_RAW)}",
        "total_60": computed["total"],
        "percent": computed["percent"],
        "confidence": payload.get("confidence", 0),
        "needs_review": payload.get("needs_review", False),
        "review_reasons": "; ".join(payload.get("review_reasons", [])),
        "comments": len(payload.get("comments", [])),
        "annotated": artifacts.get("annotated", ""),
        "form": artifacts.get("form", ""),
        "result_json": artifacts.get("result", ""),
        "model": payload["generation"]["model"],
        "generated_at": payload["generation"]["generated_at"],
        "source_pdf": payload["source"]["path"],
        "source_sha256": artifacts.get("sha256", ""),
        "status": (payload.get("confirmation") or {}).get("status", "draft"),
        "confirmed_at": (payload.get("confirmation") or {}).get("confirmed_at", ""),
    }


@dataclass
class MarkResult:
    """Artifacts and row produced for one manuscript."""

    payload: dict[str, Any]
    row: dict[str, Any]
    folder: Path
    skipped: bool = False


def mark_one(
    config: Config,
    source: str | Path,
    output_root: str | Path,
    *,
    evaluator: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
    api_key: str | None = None,
    index: dict[str, Path] | None = None,
) -> MarkResult:
    """Mark one manuscript and write its artifact folder."""
    source = Path(source)
    output_root = Path(output_root)
    digest = sha256(source)
    if not overwrite and not dry_run:
        known = index.get(digest) if index is not None else build_index(output_root).get(digest)
        if known and known.exists():
            cached = json.loads(known.read_text())
            artifacts = {
                key: cached.get("artifacts", {}).get(key, "")
                for key in ("annotated", "form", "result")
            }
            artifacts["sha256"] = digest
            return MarkResult(
                payload=cached,
                row=row_from_payload(cached, artifacts),
                folder=known.parent,
                skipped=True,
            )

    pages = extract_pages(source)
    outcome = grade(config, source, pages=pages, api_key=api_key)
    payload = outcome.payload
    evaluator_name = evaluator or config.marking.evaluator
    meta = payload["meta"]
    folder = output_root / folder_for(config, meta["matric"], meta["name"], source.stem)
    if not overwrite and folder.exists():
        existing = folder / "result.json"
        if existing.exists():
            try:
                old = json.loads(existing.read_text())
            except (json.JSONDecodeError, OSError):
                old = {}
            if old.get("source", {}).get("sha256") not in {None, "", digest}:
                base = folder
                suffix = 2
                while folder.exists():
                    folder = base.with_name(f"{base.name} ({suffix})")
                    suffix += 1

    if dry_run:
        return MarkResult(payload=payload, row=row_from_payload(payload, {}), folder=folder)

    folder.mkdir(parents=True, exist_ok=True)
    base = folder_for(config, meta["matric"], meta["name"], source.stem)
    annotated = folder / f"{base} - Annotated assignment.pdf"
    form = folder / f"{base} - Marking form.pdf"
    result_file = folder / "result.json"

    annotate_pdf(source, annotated, payload["comments"], evaluator_name, pages=pages)
    fill_form(
        config.form_template,
        form,
        {
            "student_name": meta["name"],
            "matric_number": meta["matric"],
            "title": meta.get("title", ""),
            "evaluator_name": evaluator_name,
        },
        payload,
        evaluator_name,
        config.course.semester,
        payload.get("confirmation"),
    )

    payload["annotated_by"] = evaluator_name
    payload["source"]["sha256"] = digest
    payload["artifacts"] = {
        "annotated": str(annotated),
        "form": str(form),
        "result": str(result_file),
    }
    result_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if index is not None:
        index[digest] = result_file
    artifacts = {
        "annotated": str(annotated),
        "form": str(form),
        "result": str(result_file),
        "sha256": payload["source"]["sha256"],
    }
    return MarkResult(payload=payload, row=row_from_payload(payload, artifacts), folder=folder)


class Gradebook:
    """Append-only CSV gradebook shared by concurrent batch workers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, Any]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    key = row.get("source_pdf") or row.get("matric") or row.get("name", "")
                    self._rows[key] = row

    def add(self, row: dict[str, Any]) -> None:
        key = row.get("source_pdf") or row.get("matric") or row.get("name", "")
        with self._lock:
            self._rows[key] = {field: row.get(field, "") for field in FIELDS}
            self._flush()

    def _flush(self) -> None:
        ordered = sorted(
            self._rows.values(),
            key=lambda r: (str(r.get("matric") or ""), str(r.get("name") or "")),
        )
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(ordered)
        temporary.replace(self.path)

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._rows.values())
