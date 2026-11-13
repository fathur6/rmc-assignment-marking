"""Build the marking prompt, call the model, and validate its JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from .config import Config
from .extract import PageText, extract_pages, manuscript_text, resolve_anchor
from .llm import LLMError, chat_json, parse_json_object
from .rubric import CRITERIA, band_for, compute, validate_score

PROMPT_VERSION = "rmc-2026.2"

_SECTION_MARKERS = re.compile(
    r"\b(introduction|background|latar belakang|problem statement|pernyataan masalah|"
    r"rumusan masalah|research questions?|persoalan kajian|soalan kajian|objectives?|"
    r"objektif|tujuan|literature review|sorotan literatur|ulasan literatur|kajian literatur|"
    r"significance of the study|kepentingan kajian|kepentingan penyelidikan|methodology|"
    r"metodologi|methods?|kaedah|references?|rujukan|bibliografi)\b",
    re.IGNORECASE,
)

_ANCHORING_SECTIONS = {
    "introduction",
    "background",
    "latar belakang",
    "literature review",
    "sorotan literatur",
    "ulasan literatur",
    "kajian literatur",
    "reference",
    "references",
    "rujukan",
    "bibliografi",
}

_THESIS_MARKERS = re.compile(
    r"(tesis\s+yang\s+dikemukakan|doktor\s+falsafah|doctor\s+of\s+philosophy|"
    r"ph\.?\s?d\.?\s+thesis|disertasi)",
    re.IGNORECASE,
)


def check_text(text: str, config: Config) -> None:
    """Reject image-only PDFs, theses, and documents that are not proposals."""
    stripped = text.strip()
    if len(stripped) < config.marking.min_text_chars:
        raise ValueError(
            f"insufficient extractable text ({len(stripped)} characters); "
            "the PDF is probably scanned and needs OCR or manual marking"
        )
    if _THESIS_MARKERS.search(stripped[:2500]):
        raise ValueError(
            "document looks like a thesis or dissertation, not a PPS10203 course assignment"
        )
    sections = {match.group(0).lower() for match in _SECTION_MARKERS.finditer(stripped)}
    if len(sections) < 2 or not (sections & _ANCHORING_SECTIONS):
        raise ValueError(
            "document does not look like a research proposal: missing an introduction, "
            f"literature review, or reference section (found {sorted(sections) or 'none'})"
        )

ALLOWED_CATEGORIES = {
    "Title",
    "Organisation",
    "Alignment",
    "Language",
    "Literature",
    "Methodology",
    "Presentation",
    "Citation",
    "Integrity",
    "Ethics",
    "Feedback",
}


@dataclass
class GradeOutcome:
    """Validated marking payload plus audit metadata."""

    payload: dict[str, Any]
    usage: dict[str, int]
    model: str
    elapsed_seconds: float
    issues: list[str] = field(default_factory=list)


def _rubric_table() -> str:
    header = "| Criterion | 0-1 Very weak | 2-3 Weak | 4-5 Satisfactory | 6-8 Good | 9-10 Excellent |"
    sep = "|---|---|---|---|---|---|"
    rows = []
    for criterion in CRITERIA:
        name = criterion.name
        if criterion.evaluates:
            name += " (evaluate: " + ", ".join(criterion.evaluates) + ")"
        rows.append(
            "| " + " | ".join([name, *criterion.descriptors]) + " |"
        )
    return "\n".join([header, sep, *rows])


def _scoring_anchors() -> str:
    return """
Apply these anchors consistently:

1. Organisation and coherence (CLO2). Check whether all proposal components are present
   (title, introduction/background, problem statement, significance, research questions,
   objectives, literature review, methodology, timeline/Gantt, ethics, references), whether
   they appear in a logical order, and whether each section does the job its heading promises.
   9-10 only for a proposal that reads as one coherent argument with professional structure.

2. Clarity and alignment of research components (CLO2). Check title specificity and scope,
   problem-gap consistency, one-to-one alignment of research questions to objectives, and
   whether the methodology can actually answer those questions. Conflicting question sets,
   an unoperationalised title, or a design that does not match the questions cap this mark
   at 5; award 5 when those are the only defects in an otherwise complete proposal. Deduct
   below 5 only where the misalignment is pervasive (e.g. the questions cannot be answered
   by any design described, or several components contradict each other).

3. Use of literature (CLO2). Check relevance, integration into the argument, critical
   synthesis, recency, and whether the gap is established rather than asserted. A review
   that is one descriptive paragraph per factor cannot exceed 5. An inventory-style review
   that lists relevant sources category by category, without synthesis, matches the
   Satisfactory descriptor itself: award 5. Its descriptiveness is not an independent second
   defect, so do not move to 4 for it. Award 4 only when the sources are also marginally
   relevant or absent from the argument, and award 6-8 only for actual synthesis. Reward
   comparison, critique, and theory building.

4. Academic integrity and citation practice (CLO3). Check in-text citations against the
   reference list in both directions, APA consistency, attribution of specific claims,
   garbled or fabricated entries, and uncited sources. A reference list that is complete and
   consistently formatted in APA style, with a handful of entries uncited or a few citations
   lacking entries, is 6 (Good). Award 4-5 when the practice itself is inconsistent: mixed
   formats, major claims uncited, or a list that cannot be matched to the text at all.

5. Professional academic practice and ethical awareness (CLO3). Check academic tone and
   presentation, and whether ethics is study-specific: informed consent, anonymity,
   confidentiality, data storage and retention, approval pathway, and risk mitigation.
   Generic boilerplate that could belong to any study cannot exceed 5.

Band anchoring (apply before assigning any mark):
- Match the evidence to one descriptor row, then start at the band's anchor: 1 for Very
  weak, 3 for Weak, 5 for Satisfactory, 6 for Good, 9 for Excellent.
- Move down within the band only for an independent second defect, and up only for explicit
  evidence of the higher descriptor.
- Do not place work at the floor of a band because the descriptor can be read negatively;
  place it at the anchor when it fits, and justify any move.
- If your evidence for a complete-but-generic proposal matches the calibration note, your
  marks should land within one point of its 6, 5, 5, 6, 5.

Boundary examples (the calibration proposal's facts map to these marks; apply the same
mapping when another script shows the same pattern):
- All components present, logical order, overlong problem statement: organisation 6.
- Two competing question sets, broad title, undefined success/failure: alignment 5.
- Relevant sources, one descriptive paragraph per factor, no synthesis: literature 5.
- Complete reference list in consistent APA style with five uncited entries and two cited
  works missing entries: integrity 6. This pattern is Good, not Satisfactory; the formatting
  itself is consistent.
- Generic ethics section with optional approval and unnamed committee: ethics 5.

Cross-criterion rules:
- If the methodology lacks a design, sampling frame, sample size, instruments, or analysis
  plan, clarity/alignment cannot exceed 6 and organisation cannot exceed 7.
- If most references are uncited or most citations are missing, integrity cannot exceed 5.
- Do not let a strong literature review raise integrity or ethics; they are separate criteria.
- Do not award 9-10 to any criterion without specific, quoted evidence of excellence.
""".strip()


def _comment_rules(config: Config) -> str:
    return f"""
Margin comments:
- Produce between {config.marking.comments_min} and {config.marking.comments_max} comments in total.
- Cover both CLOs: at least one comment per criterion, and at least two CLO3 comments
  (one on citation/integrity, one on ethics/professional practice).
- Each comment must point at a specific passage. `anchor` is an exact, continuous quote of
  4-18 words copied from the page text, without ellipses, quotation marks, or added words.
  Choose the earliest occurrence of the problem. `page` is the page number containing it.
- `comment` is 1-3 sentences (40-450 characters) of specific, actionable feedback: name the
  problem, say why it matters for the criterion, and state the fix. No generic praise.
- `clo` is "CLO2" or "CLO3"; `category` is one of: {", ".join(sorted(ALLOWED_CATEGORIES))}.
- Prefer comments on substantive issues (alignment, methodology, literature, citation,
  ethics) over cosmetic language fixes; include at most two language/mechanics comments.
- Do not repeat the same point on several pages. One comment per distinct issue.
""".strip()


def _schema() -> str:
    return """
Return this JSON object (all keys required):

{
  "student_name": "full name as printed on the title page, or empty string",
  "matric_number": "matric number as printed, or empty string",
  "title": "proposal title without surrounding quotation marks",
  "criteria": {
    "clo2_org":      {"score": 6, "evidence": "page and quoted evidence", "rationale": "<= 50 words"},
    "clo2_align":    {"score": 5, "evidence": "...", "rationale": "..."},
    "clo2_lit":      {"score": 5, "evidence": "...", "rationale": "..."},
    "clo3_integrity":{"score": 6, "evidence": "...", "rationale": "..."},
    "clo3_ethics":   {"score": 5, "evidence": "...", "rationale": "..."}
  },
  "comments": [
    {"page": 1, "anchor": "exact quote from page 1", "clo": "CLO2", "category": "Title", "comment": "..."}
  ],
  "strengths": ["3-5 specific strengths, each with evidence"],
  "improvements": ["4-6 prioritised, actionable improvements"],
  "confidence": 0.82,
  "needs_review": false,
  "review_reason": ""
}

Rules: scores are integers 0-10. `evidence` names at least one page and quotes the text it
relies on. `rationale` explains the band placement, not the whole proposal. `confidence` is
0-1 and reflects extraction quality and your certainty. Set `needs_review` true with a short
`review_reason` when the text is incomplete or garbled, the manuscript may be the wrong
document, or the evidence conflicts.
""".strip()


def build_messages(
    config: Config, manuscript: str, calibration: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Assemble the system and user messages for one manuscript."""
    system = (config.root / "prompts" / "system.md").read_text()
    calibration_block = ""
    if calibration:
        calibration_block = (
            "\nCalibration example (moderated reference; use it to anchor band boundaries, "
            "not to copy its marks):\n```json\n"
            + json.dumps(calibration, ensure_ascii=False, indent=1)
            + "\n```\n"
        )
    user = f"""Mark this PPS10203 Research Methodology proposal.

Course: {config.course.code} {config.course.title}, {config.course.semester}.
Assignment weight: {config.course.assignment_weight}% of the course; the final examination is {config.course.final_exam_weight}%.

The manuscript may be written in English, Malay, or Indonesian. Judge the research substance,
not the language choice. Quote anchors verbatim in the original language. Write `comment`,
`strengths`, and `improvements` in the dominant language of the manuscript so the student can
act on them; keep JSON keys, `clo`, and `category` values in English.

Official rubric:
{_rubric_table()}

{_scoring_anchors()}

{_comment_rules(config)}

{_schema()}
{calibration_block}
Manuscript, page by page:

{manuscript}
"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _coerce_score(value: Any) -> int:
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if not match:
            raise ValueError(f"no number in score {value!r}")
        value = int(match.group(0))
    return validate_score(value)


def _clean_anchor(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip("“”\"'` ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def metadata_author(source: str | Path) -> str:
    """Best-effort name from the PDF author field, used only when extraction fails."""
    try:
        with pymupdf.open(source) as doc:
            author = str((doc.metadata or {}).get("author") or "").strip()
    except (RuntimeError, ValueError, OSError):
        return ""
    noisy = ("microsoft", "administrator", "admin", "user", "unknown", "asus", "dell", "hp")
    if 3 <= len(author) <= 80 and not any(token in author.lower() for token in noisy):
        return author
    return ""


def _filename_meta(path: Path) -> dict[str, str]:
    stem = path.stem
    bare = re.match(r"^\s*([A-Za-z]{1,3}\d{3,})\s*$", stem)
    if bare:
        return {"matric": bare.group(1).strip(), "name": ""}
    match = re.match(
        r"^\s*([A-Za-z]{1,3}\d{3,})\s*[-–_]\s*(.+?)(?:\s+assignment)?$",
        stem,
        re.IGNORECASE,
    )
    if match:
        return {"matric": match.group(1).strip(), "name": match.group(2).strip()}
    return {"matric": "", "name": ""}


def validate_payload(
    payload: dict[str, Any], pages: list[PageText], config: Config, source: Path
) -> tuple[dict[str, Any], list[str]]:
    """Normalize the model payload and collect quality issues."""
    issues: list[str] = []
    criteria_raw = payload.get("criteria")
    if not isinstance(criteria_raw, dict):
        raise ValueError("criteria must be an object")
    criteria: dict[str, Any] = {}
    for criterion in CRITERIA:
        entry = criteria_raw.get(criterion.id)
        if not isinstance(entry, dict):
            raise ValueError(f"missing criterion {criterion.id}")
        score = _coerce_score(entry.get("score"))
        criteria[criterion.id] = {
            "score": score,
            "band": band_for(score),
            "evidence": str(entry.get("evidence", "")).strip(),
            "rationale": str(entry.get("rationale", "")).strip(),
        }

    fallback = _filename_meta(source)
    name = str(payload.get("student_name") or "").strip() or fallback["name"]
    if not name:
        author = metadata_author(source)
        if author:
            name = author
            issues.append("name taken from PDF metadata")
    matric = re.sub(r"\s+", "", str(payload.get("matric_number") or "")).upper() or fallback["matric"].upper()
    title = str(payload.get("title") or "").strip().strip("“”\"'")

    comments: list[dict[str, Any]] = []
    raw_comments = payload.get("comments")
    if not isinstance(raw_comments, list):
        raise ValueError("comments must be an array")
    for index, item in enumerate(raw_comments):
        if not isinstance(item, dict):
            continue
        anchor = _clean_anchor(item.get("anchor"))
        comment = re.sub(r"\s+", " ", str(item.get("comment") or "")).strip()
        if len(anchor) < 8 or len(comment) < 20:
            issues.append(f"comment {index} dropped: too short")
            continue
        try:
            page_no = int(item.get("page", 1))
        except (TypeError, ValueError):
            page_no = 1
        clo = str(item.get("clo") or "CLO2").upper()
        if clo not in {"CLO2", "CLO3"}:
            clo = "CLO2" if str(item.get("category")) in {"Title", "Organisation", "Alignment", "Literature", "Methodology"} else "CLO3"
        category = str(item.get("category") or "Feedback").strip()
        if category not in ALLOWED_CATEGORIES:
            category = "Feedback"
        resolved = resolve_anchor(pages, page_no, anchor)
        if resolved is None:
            issues.append(f"anchor not located: {anchor[:60]!r}")
            page_no = min(max(page_no, 1), len(pages))
        else:
            resolved_page, _match, _rects = resolved
            if resolved_page != page_no:
                issues.append(f"page corrected {page_no}->{resolved_page}: {anchor[:40]!r}")
                page_no = resolved_page
        comments.append(
            {
                "page": page_no,
                "anchor": anchor,
                "clo": clo,
                "category": category,
                "comment": comment[:600],
                "order": index,
            }
        )

    comments.sort(key=lambda c: (c["page"], c["order"]))
    strengths = [re.sub(r"\s+", " ", str(s)).strip() for s in payload.get("strengths") or [] if str(s).strip()]
    improvements = [
        re.sub(r"\s+", " ", str(s)).strip() for s in payload.get("improvements") or [] if str(s).strip()
    ]
    try:
        confidence = float(payload.get("confidence", 0.75))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = min(max(confidence, 0.0), 1.0)

    if len(comments) < config.marking.comments_min:
        issues.append(f"only {len(comments)} comments (target {config.marking.comments_min})")
    if not strengths:
        issues.append("no strengths returned")
    if not improvements:
        issues.append("no improvements returned")
    if not name or not matric:
        issues.append("student name or matric number not extracted")

    review_reasons = [
        issue
        for issue in issues
        if not issue.startswith(("page corrected", "name taken from PDF metadata"))
    ]
    if payload.get("needs_review"):
        review_reasons.append(str(payload.get("review_reason") or "model requested review"))
    if confidence < config.marking.review_confidence_threshold:
        review_reasons.append(f"confidence {confidence:.2f} below threshold")

    normalized = {
        "meta": {"name": name, "matric": matric, "title": title},
        "criteria": criteria,
        "comments": comments,
        "strengths": strengths[:6],
        "improvements": improvements[:8],
        "confidence": confidence,
        "needs_review": bool(payload.get("needs_review"))
        or confidence < config.marking.review_confidence_threshold
        or bool(review_reasons),
        "review_reasons": review_reasons,
    }
    return normalized, issues


def _repair(
    config: Config,
    messages: list[dict[str, str]],
    bad_content: str,
    error: str,
    api_key: str,
) -> tuple[dict[str, Any], dict[str, int], str, float]:
    repair_messages = [
        *messages,
        {"role": "assistant", "content": bad_content[:12000]},
        {
            "role": "user",
            "content": (
                "That JSON is invalid for the required schema: "
                f"{error}. Return the corrected complete JSON object only. "
                "Every criterion key is required, scores are integers 0-10, comments need "
                "page, anchor, clo, category, and comment."
            ),
        },
    ]
    result = chat_json(
        api_base=config.marking.api_base,
        api_key=api_key,
        model=config.marking.model,
        messages=repair_messages,
        temperature=0.0,
        max_tokens=config.marking.max_output_tokens,
        timeout=config.marking.timeout_seconds,
    )
    return parse_json_object(result.content), result.usage, result.model, result.elapsed_seconds


def grade(
    config: Config,
    source: str | Path,
    *,
    pages: list[PageText] | None = None,
    api_key: str | None = None,
    include_raw: bool = False,
) -> GradeOutcome:
    """Mark one manuscript and return a validated, fully computed payload."""
    source = Path(source)
    pages = pages or extract_pages(source)
    text = manuscript_text(pages)
    check_text(text, config)
    truncated = False
    if len(text) > config.marking.max_input_chars:
        text = text[: config.marking.max_input_chars]
        truncated = True

    calibration = None
    if config.calibration.exists():
        calibration = json.loads(config.calibration.read_text())

    messages = build_messages(config, text, calibration)
    key = api_key or config.api_key()
    result = chat_json(
        api_base=config.marking.api_base,
        api_key=key,
        model=config.marking.model,
        messages=messages,
        temperature=config.marking.temperature,
        max_tokens=config.marking.max_output_tokens,
        timeout=config.marking.timeout_seconds,
    )
    usage = dict(result.usage)
    elapsed = result.elapsed_seconds
    model = result.model

    try:
        payload = parse_json_object(result.content)
        normalized, issues = validate_payload(payload, pages, config, source)
    except (ValueError, KeyError, TypeError) as exc:
        payload, repaired_usage, model, repaired_elapsed = _repair(
            config, messages, result.content, str(exc), key
        )
        usage = {k: usage.get(k, 0) + repaired_usage.get(k, 0) for k in set(usage) | set(repaired_usage)}
        elapsed = round(elapsed + repaired_elapsed, 1)
        normalized, issues = validate_payload(payload, pages, config, source)
        issues.append(f"repaired: {exc}")

    if truncated:
        issues.append(f"input truncated at {config.marking.max_input_chars} characters")
        normalized["needs_review"] = True
        normalized["review_reasons"].append("input truncated")

    scores = {cid: normalized["criteria"][cid]["score"] for cid in normalized["criteria"]}
    normalized["computed"] = compute(scores)
    normalized["source"] = {
        "path": str(source.resolve()),
        "pages": len(pages),
        "characters": len(text),
    }
    normalized["generation"] = {
        "module": "RMC Assignment Marking Module",
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "usage": usage,
        "elapsed_seconds": elapsed,
        "issues": issues,
    }
    if include_raw:
        normalized["model_output"] = payload
    return GradeOutcome(
        payload=normalized,
        usage=usage,
        model=model,
        elapsed_seconds=elapsed,
        issues=issues,
    )


__all__ = ["GradeOutcome", "grade", "build_messages", "validate_payload", "LLMError"]
