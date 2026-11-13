"""Batch discovery, concurrent marking, and run summaries."""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Config
from .mark import Gradebook, MarkResult, build_index, mark_one
from .report import write_review_html


def discover(inputs: Iterable[str], pattern: str = "*.pdf") -> list[Path]:
    """Expand files, directories, and glob patterns into a sorted PDF list."""
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            found.extend(sorted(path.rglob(pattern)))
        elif path.is_file():
            found.append(path)
        else:
            matches = sorted(Path().glob(raw))
            found.extend(matches)
    unique: dict[Path, None] = {}
    for path in found:
        if path.suffix.lower() != ".pdf":
            continue
        lowered = path.name.lower()
        if "annotated assignment" in lowered or "marking form" in lowered:
            continue
        unique[path.resolve()] = None
    return list(unique)


@dataclass
class BatchReport:
    """Aggregate outcome of one batch run."""

    processed: int = 0
    skipped: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    seconds: float = 0.0


def run_batch(
    config: Config,
    inputs: Iterable[str],
    output_root: str | Path,
    *,
    evaluator: str | None = None,
    limit: int | None = None,
    concurrency: int | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
    pattern: str = "*.pdf",
    api_key: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> BatchReport:
    """Mark every discovered manuscript, resuming finished work by default."""
    output_root = Path(output_root)
    input_list = list(inputs)
    files = discover(input_list, pattern)
    if limit:
        files = files[:limit]
    report = BatchReport()
    if not files:
        return report

    out_resolved = output_root.resolve()
    input_roots = [Path(item).expanduser().resolve() for item in input_list if Path(item).expanduser().is_dir()]
    files = [
        path
        for path in files
        if out_resolved not in path.parents
        or any(root in path.parents or root == path.parent for root in input_roots)
    ]
    gradebook = Gradebook(output_root / "gradebook.csv")
    index = build_index(output_root)
    started = time.time()
    done_lock = threading.Lock()
    finished = 0
    total = len(files)

    def emit(message: str) -> None:
        if progress:
            progress(message)

    def work(path: Path) -> MarkResult:
        return mark_one(
            config,
            path,
            output_root,
            evaluator=evaluator,
            dry_run=dry_run,
            overwrite=overwrite,
            api_key=api_key,
            index=index,
        )

    workers = max(1, concurrency or config.batch.concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, path): path for path in files}
        for future in as_completed(futures):
            path = futures[future]
            with done_lock:
                finished += 1
                position = f"[{finished}/{total}]"
            try:
                result = future.result()
            except Exception as exc:  # batch isolation: one failure must not stop the run
                report.failed.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})
                emit(f"{position} FAILED {path.name}: {type(exc).__name__}: {exc}")
                continue
            if result.skipped:
                report.skipped += 1
                emit(f"{position} skipped (up to date) {path.name}")
                continue
            report.processed += 1
            report.rows.append(result.row)
            report.tokens += int(result.payload.get("generation", {}).get("usage", {}).get("total_tokens", 0))
            if not dry_run:
                gradebook.add(result.row)
            total_mark = result.payload["computed"]["total"]
            percent = result.payload["computed"]["percent"]
            flag = " REVIEW" if result.payload.get("needs_review") else ""
            emit(
                f"{position} {result.row['matric'] or '?'} {result.row['name'][:32]:32} "
                f"{total_mark:5.2f}/60 ({percent:4.1f}%){flag}"
            )

    report.seconds = round(time.time() - started, 1)
    if not dry_run:
        summary = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "course": f"{config.course.code} {config.course.title}",
            "semester": config.course.semester,
            "evaluator": evaluator or config.marking.evaluator,
            "model": config.marking.model,
            "files_found": total,
            "processed": report.processed,
            "skipped": report.skipped,
            "failed": report.failed,
            "total_tokens": report.tokens,
            "seconds": report.seconds,
        }
        (output_root / "batch-summary.json").write_text(json.dumps(summary, indent=2))
        write_review_html(output_root, title="RMC Assignment Marking - Review")
    return report


def print_report(report: BatchReport, stream=sys.stderr) -> None:
    """Print the end-of-batch summary."""
    stream.write(
        f"\nprocessed {report.processed}, skipped {report.skipped}, failed {len(report.failed)}, "
        f"tokens {report.tokens}, {report.seconds:.1f}s\n"
    )
    for failure in report.failed:
        stream.write(f"  FAILED {failure['file']}: {failure['error']}\n")
