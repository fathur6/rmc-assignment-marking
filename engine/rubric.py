"""The official PPS10203 assignment rubric.

One source of truth for criterion ids, band names, score weights, and the
marking-form layout. Both the prompt builder and the PDF renderers import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

CLO2_WEIGHT: Final = 40.0
CLO3_WEIGHT: Final = 20.0
CLO2_MAX_RAW: Final = 30.0
CLO3_MAX_RAW: Final = 20.0
ASSIGNMENT_MAX: Final = 60.0

BANDS: Final[tuple[tuple[int, int, str], ...]] = (
    (0, 1, "Very weak"),
    (2, 3, "Weak"),
    (4, 5, "Satisfactory"),
    (6, 8, "Good"),
    (9, 10, "Excellent"),
)


@dataclass(frozen=True)
class Criterion:
    """One rubric row, its CLO, and what the marker evaluates."""

    id: str
    clo: str
    name: str
    evaluates: tuple[str, ...]
    descriptors: tuple[str, ...]  # Very weak -> Excellent, five entries.


CRITERIA: Final[tuple[Criterion, ...]] = (
    Criterion(
        id="clo2_org",
        clo="CLO2",
        name="Organisation and coherence of proposal writing",
        evaluates=(),
        descriptors=(
            "Proposal components are incomplete and lack logical flow.",
            "Components presented with limited organisation and weak linkage.",
            "Components generally organised but coherence requires improvement.",
            "Components organised logically with clear flow between sections.",
            "Proposal demonstrates highly coherent and professionally structured academic writing.",
        ),
    ),
    Criterion(
        id="clo2_align",
        clo="CLO2",
        name="Clarity and alignment of research components",
        evaluates=(
            "Title",
            "Problem statement",
            "Objectives",
            "Methodology",
        ),
        descriptors=(
            "Components are unclear, inconsistent and poorly aligned.",
            "Components demonstrate limited alignment and require substantial clarification.",
            "Components show moderate alignment but some inconsistencies remain.",
            "Components are clearly written and demonstrate logical alignment.",
            "Components are exceptionally clear, focused and demonstrate excellent alignment throughout the proposal.",
        ),
    ),
    Criterion(
        id="clo2_lit",
        clo="CLO2",
        name="Use of literature to support academic writing",
        evaluates=("Relevance", "Integration", "Academic discussion"),
        descriptors=(
            "Literature is absent, irrelevant or unrelated to the proposed study.",
            "Limited literature is used with minimal integration into discussion.",
            "Literature is generally relevant but discussion remains descriptive.",
            "Relevant literature is integrated appropriately to support academic arguments.",
            "Literature is critically selected, synthesised and effectively supports academic discussion and proposal justification.",
        ),
    ),
    Criterion(
        id="clo3_integrity",
        clo="CLO3",
        name="Academic integrity and citation practice",
        evaluates=(),
        descriptors=(
            "Significant citation errors and poor academic integrity practices are evident.",
            "Referencing is inconsistent and citation practice requires substantial improvement.",
            "Citation and referencing are generally acceptable with several inaccuracies.",
            "Citation and referencing are applied appropriately and consistently.",
            "Demonstrates excellent academic integrity with accurate and professional citation practice.",
        ),
    ),
    Criterion(
        id="clo3_ethics",
        clo="CLO3",
        name="Professional academic practice and ethical awareness",
        evaluates=(),
        descriptors=(
            "Proposal lacks professionalism and demonstrates minimal ethical awareness.",
            "Proposal is inconsistent and ethical considerations are insufficient.",
            "Proposal is acceptable with basic ethical awareness.",
            "Proposal is professionally presented with appropriate ethical consideration.",
            "Proposal demonstrates excellent professionalism and strong awareness of ethical research practice.",
        ),
    ),
)

CRITERION_BY_ID: Final = {c.id: c for c in CRITERIA}


def band_for(score: int) -> str:
    """Return the rubric band name for an integer mark 0-10."""
    for low, high, name in BANDS:
        if low <= score <= high:
            return name
    raise ValueError(f"score out of range: {score}")


def validate_score(score: object) -> int:
    """Coerce a model mark to an integer 0-10, rejecting anything else."""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError(f"score is not a number: {score!r}")
    if float(score) != int(score):
        raise ValueError(f"score is not an integer: {score!r}")
    value = int(score)
    if not 0 <= value <= 10:
        raise ValueError(f"score outside 0-10: {score!r}")
    return value


def compute(scores: dict[str, int]) -> dict[str, object]:
    """Compute CLO subtotals, weighted marks, total, and percentage."""
    missing = set(CRITERION_BY_ID) - set(scores)
    if missing:
        raise ValueError(f"missing criteria: {sorted(missing)}")
    clo2 = sum(scores[c.id] for c in CRITERIA if c.clo == "CLO2")
    clo3 = sum(scores[c.id] for c in CRITERIA if c.clo == "CLO3")
    clo2_weighted = round(clo2 / CLO2_MAX_RAW * CLO2_WEIGHT, 2)
    clo3_weighted = round(clo3 / CLO3_MAX_RAW * CLO3_WEIGHT, 2)
    total = round(clo2_weighted + clo3_weighted, 2)
    return {
        "clo2_raw": clo2,
        "clo3_raw": clo3,
        "clo2_weighted": clo2_weighted,
        "clo3_weighted": clo3_weighted,
        "total": total,
        "percent": round(total / ASSIGNMENT_MAX * 100, 1),
    }
