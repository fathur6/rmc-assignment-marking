"""Command-line interface for the RMC Assignment Marking Module."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .annotate import annotate_pdf
from .batch import print_report, run_batch
from .config import Config, load_config, output_root_for
from .extract import extract_pages
from .form import fill_form
from .grade import grade, metadata_author
from .mark import Gradebook, mark_one, row_from_payload, sha256
from .report import write_review_html


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--out", help="output directory")


def _add_marking(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evaluator", help="evaluator name printed on artifacts")
    parser.add_argument(
        "--model",
        help="model id override (default: marking.model in config.yaml; e.g. deepseek-v4-pro)",
    )
    parser.add_argument("--dry-run", action="store_true", help="grade and print, write no files")
    parser.add_argument("--overwrite", action="store_true", help="replace existing artifacts")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rmc",
        description="RMC Assignment Marking Module: mark PPS10203 proposals at scale.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subs = parser.add_subparsers(dest="command", required=True)

    doctor = subs.add_parser("doctor", help="check configuration, credentials and dependencies")
    _add_common(doctor)

    mark = subs.add_parser("mark", help="mark one or more manuscript PDFs")
    mark.add_argument("pdfs", nargs="+")
    _add_common(mark)
    _add_marking(mark)
    mark.add_argument("--json", action="store_true", help="print the full result JSON")

    batch = subs.add_parser("batch", help="mark a folder of manuscripts (default: configured inbox)")
    batch.add_argument("inputs", nargs="*")
    _add_common(batch)
    _add_marking(batch)
    batch.add_argument("--concurrency", type=int, help="parallel workers (default: config)")
    batch.add_argument("--limit", type=int, help="process at most N manuscripts")
    batch.add_argument("--pattern", default="*.pdf", help="glob used inside input folders")

    finalize = subs.add_parser("finalize", help="confirm marks as final and regenerate forms")
    finalize.add_argument("results", nargs="+")
    _add_common(finalize)
    finalize.add_argument("--by", help="name of the person confirming (default: evaluator)")
    finalize.add_argument("--name", help="override a missing student name")

    regen = subs.add_parser("regen", help="rebuild PDFs from saved result.json files")
    regen.add_argument("results", nargs="+")
    _add_common(regen)
    regen.add_argument("--evaluator", help="evaluator name printed on artifacts")

    report = subs.add_parser("report", help="rebuild review.html from gradebook.csv")
    report.add_argument("output", help="batch output directory")
    _add_common(report)

    return parser


def _mask(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 8 else "set"


def _api_probe(config: Config, key: str) -> tuple[bool, str]:
    url = config.marking.api_base.rstrip("/") + "/models"
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        names = [item.get("id") for item in data.get("data", [])]
        return True, ", ".join(str(name) for name in names)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print("RMC Assignment Marking Module", __version__)
    print("root:", config.root)
    print("python:", sys.version.split()[0])
    for module in ("pymupdf", "reportlab", "yaml"):
        try:
            imported = __import__(module)
            print(f"dependency {module}: {getattr(imported, '__version__', 'ok')}")
        except ImportError as exc:
            print(f"dependency {module}: MISSING ({exc})")
    print("form template:", config.form_template, "exists" if config.form_template.exists() else "MISSING")
    print("calibration:", config.calibration, "exists" if config.calibration.exists() else "missing")
    print("model:", config.marking.model, "| evaluator:", config.marking.evaluator)
    try:
        key = config.api_key()
        print("api key:", _mask(key), f"(${config.marking.api_key_env})")
        ok, detail = _api_probe(config, key)
        print("api probe:", "ok - " + detail if ok else "FAILED - " + detail)
    except RuntimeError as exc:
        print("api key: MISSING -", exc)
        return 1
    print("output root:", output_root_for(config, args.out))
    return 0


def cmd_mark(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.model:
        config.marking.model = args.model
    output_root = output_root_for(config, args.out)
    key = config.api_key()
    gradebook = Gradebook(output_root / "gradebook.csv") if not args.dry_run else None
    failures = 0
    for raw in args.pdfs:
        source = Path(raw).expanduser()
        if not source.exists():
            print(f"missing: {source}", file=sys.stderr)
            failures += 1
            continue
        started = time.time()
        try:
            result = mark_one(
                config,
                source,
                output_root,
                evaluator=args.evaluator,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
                api_key=key,
            )
        except Exception as exc:  # one bad file must not abort the others
            print(f"FAILED {source.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        payload = result.payload
        computed = payload["computed"]
        scores = " / ".join(str(payload["criteria"][cid]["score"]) for cid in payload["criteria"])
        flag = " REVIEW" if payload.get("needs_review") else ""
        print(
            f"{payload['meta']['matric'] or '?'} {payload['meta']['name'] or source.stem}: "
            f"{scores} | {computed['total']:.2f}/60 ({computed['percent']:.1f}%){flag} "
            f"[{time.time() - started:.1f}s]"
        )
        for reason in payload.get("review_reasons", []):
            print("  review:", reason)
        if result.skipped:
            print("  skipped: artifacts already current")
        elif not args.dry_run:
            print("  annotated:", payload["artifacts"]["annotated"])
            print("  form:     ", payload["artifacts"]["form"])
            if gradebook:
                gradebook.add(result.row)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    if gradebook and not args.dry_run:
        write_review_html(output_root)
    return 1 if failures else 0


def cmd_batch(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.model:
        config.marking.model = args.model
    output_root = output_root_for(config, args.out)
    inputs = args.inputs or [str(config.inbox_root)]
    if not args.inputs:
        config.inbox_root.mkdir(parents=True, exist_ok=True)
        print(f"inbox: {config.inbox_root}", file=sys.stderr)
    key = config.api_key()
    report = run_batch(
        config,
        inputs,
        output_root,
        evaluator=args.evaluator,
        limit=args.limit,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        pattern=args.pattern,
        api_key=key,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print_report(report)
    if not args.dry_run:
        print("gradebook:", output_root / "gradebook.csv")
        print("review:   ", output_root / "review.html")
    return 1 if report.failed else 0


def cmd_regen(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    evaluator = args.evaluator or config.marking.evaluator
    failures = 0
    for raw in args.results:
        path = Path(raw).expanduser()
        try:
            payload = json.loads(path.read_text())
            source = Path(payload["source"]["path"])
            if not source.exists():
                raise FileNotFoundError(f"source manuscript not found: {source}")
            pages = extract_pages(source)
            meta = payload["meta"]
            base = path.parent.name or source.stem
            annotated = path.parent / f"{base} - Annotated assignment.pdf"
            form = path.parent / f"{base} - Marking form.pdf"
            annotate_pdf(source, annotated, payload["comments"], evaluator, pages=pages)
            fill_form(
                config.form_template,
                form,
                {
                    "student_name": meta.get("name", ""),
                    "matric_number": meta.get("matric", ""),
                    "title": meta.get("title", ""),
                    "evaluator_name": evaluator,
                },
                payload,
                evaluator,
                config.course.semester,
            )
            payload.setdefault("source", {})["sha256"] = sha256(source)
            payload["artifacts"] = {
                "annotated": str(annotated),
                "form": str(form),
                "result": str(path),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            print(f"regenerated {meta.get('matric') or '?'} {meta.get('name') or ''}: {annotated.name}")
        except Exception as exc:
            print(f"FAILED {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def cmd_finalize(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    confirmer = args.by or config.marking.evaluator
    gradebooks: dict[Path, Gradebook] = {}
    failures = 0
    for raw in args.results:
        path = Path(raw).expanduser()
        try:
            payload = json.loads(path.read_text())
            source = Path(payload["source"]["path"])
            if not source.exists():
                raise FileNotFoundError(f"source manuscript not found: {source}")
            meta = payload.setdefault("meta", {})
            if args.name:
                meta["name"] = args.name
            elif not meta.get("name"):
                author = metadata_author(source)
                if author:
                    meta["name"] = author
            payload["confirmation"] = {
                "status": "final",
                "confirmed_by": confirmer,
                "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            pages = extract_pages(source)
            base = path.parent.name
            annotated = path.parent / f"{base} - Annotated assignment.pdf"
            form = path.parent / f"{base} - Marking form.pdf"
            annotate_pdf(source, annotated, payload["comments"], confirmer, pages=pages)
            fill_form(
                config.form_template,
                form,
                {
                    "student_name": meta.get("name", ""),
                    "matric_number": meta.get("matric", ""),
                    "title": meta.get("title", ""),
                    "evaluator_name": confirmer,
                },
                payload,
                confirmer,
                config.course.semester,
                payload["confirmation"],
            )
            payload.setdefault("source", {})["sha256"] = sha256(source)
            payload["artifacts"] = {
                "annotated": str(annotated),
                "form": str(form),
                "result": str(path),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            root = path.parent.parent
            gradebook = gradebooks.setdefault(root, Gradebook(root / "gradebook.csv"))
            artifacts = {
                "annotated": str(annotated),
                "form": str(form),
                "result": str(path),
                "sha256": payload["source"]["sha256"],
            }
            gradebook.add(row_from_payload(payload, artifacts))
            print(
                f"final {meta.get('matric') or '?'} {meta.get('name') or ''}: "
                f"{payload['computed']['total']:.2f}/60"
            )
        except Exception as exc:
            print(f"FAILED {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
    for root in gradebooks:
        write_review_html(root)
    return 1 if failures else 0


def cmd_report(args: argparse.Namespace) -> int:
    target = write_review_html(args.output)
    print("review:", target)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "doctor": cmd_doctor,
        "mark": cmd_mark,
        "batch": cmd_batch,
        "finalize": cmd_finalize,
        "regen": cmd_regen,
        "report": cmd_report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
