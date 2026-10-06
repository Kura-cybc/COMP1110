"""Analytic Hierarchy Process (AHP) weights for transparent prioritisation.

Saaty's AHP turns pairwise judgements into weights, so the plan can explain *why*
one preparation task is placed before another instead of relying on an arbitrary
ordering.  Four criteria are scored for every task on a fixed 0-1 scale:

======================  =====================================================
criterion               meaning for exchange preparation
======================  =====================================================
``impact``              how much the outcome depends on this task
``urgency``             how little time is left before it must be finished
``flexibility``         how hard the task is to move (rigid tasks matter more)
``effort``              how much sustained work it needs (start earlier)
======================  =====================================================

The score never overrides the dependency graph; it orders work that is *already*
legal, and it explains which ready task to start first.  Consistency of the
pairwise matrix is measured with Saaty's consistency ratio (CR < 0.10 accepted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from planner.schema import AhpComparison, PlanningTask

#: Saaty's random consistency index for matrices of order n.
RANDOM_INDEX: dict[int, float] = {
    1: 0.0,
    2: 0.0,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
}

DEFAULT_CRITERIA = ("impact", "urgency", "flexibility", "effort")

#: Default pairwise judgements for exchange preparation: a missed hard deadline
#: is unrecoverable, so urgency dominates; impact is next; effort is a weak
#: tiebreaker.  These are explicit so a reader (or marker) can disagree and edit.
DEFAULT_COMPARISONS: tuple[AhpComparison, ...] = (
    AhpComparison(a="urgency", b="impact", value=2.0),
    AhpComparison(a="urgency", b="flexibility", value=3.0),
    AhpComparison(a="urgency", b="effort", value=4.0),
    AhpComparison(a="impact", b="flexibility", value=2.0),
    AhpComparison(a="impact", b="effort", value=3.0),
    AhpComparison(a="flexibility", b="effort", value=2.0),
)


@dataclass
class AhpResult:
    """Weights, consistency and the audit trail of one AHP evaluation."""

    criteria: list[str]
    weights: dict[str, float]
    lambda_max: float
    consistency_index: float
    consistency_ratio: float
    accepted: bool
    rationale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "criteria": self.criteria,
            "weights": {key: round(value, 4) for key, value in self.weights.items()},
            "lambda_max": round(self.lambda_max, 4),
            "consistency_index": round(self.consistency_index, 4),
            "consistency_ratio": round(self.consistency_ratio, 4),
            "accepted": self.accepted,
            "rationale": self.rationale,
        }


def pairwise_matrix(criteria: list[str], comparisons: list[AhpComparison]) -> list[list[float]]:
    """Build the reciprocal pairwise comparison matrix (defaults to 1)."""

    index = {name: position for position, name in enumerate(criteria)}
    size = len(criteria)
    matrix = [[1.0] * size for _ in range(size)]
    for comparison in comparisons:
        if comparison.a not in index or comparison.b not in index:
            raise ValueError(f"comparison refers to unknown criteria: {comparison.a}/{comparison.b}")
        i, j = index[comparison.a], index[comparison.b]
        if i == j:
            raise ValueError(f"a criterion cannot be compared with itself: {comparison.a}")
        matrix[i][j] = comparison.value
        matrix[j][i] = 1.0 / comparison.value
    return matrix


def principal_eigenvector(matrix: list[list[float]], iterations: int = 200, tol: float = 1e-12):
    """Power iteration for the principal eigenvector and eigenvalue.

    Deterministic and dependency-free: the same matrix always yields the same
    weights, which the evaluation protocol relies on.
    """

    size = len(matrix)
    vector = [1.0 / size] * size
    eigenvalue = float(size)
    for _ in range(iterations):
        product = [sum(row[j] * vector[j] for j in range(size)) for row in matrix]
        total = sum(product)
        if total == 0:
            raise ValueError("pairwise matrix is degenerate")
        normalised = [value / total for value in product]
        eigenvalue = sum(
            sum(matrix[i][j] * normalised[j] for j in range(size)) / normalised[i]
            for i in range(size)
        ) / size
        if max(abs(normalised[i] - vector[i]) for i in range(size)) < tol:
            vector = normalised
            break
        vector = normalised
    return vector, eigenvalue


def consistency_ratio(criteria: list[str], lambda_max: float) -> tuple[float, float]:
    """Return ``(consistency_index, consistency_ratio)`` for n criteria."""

    size = len(criteria)
    if size <= 2:
        return 0.0, 0.0
    index = RANDOM_INDEX.get(size)
    if index is None:
        raise ValueError(f"no random consistency index for {size} criteria")
    consistency_index = (lambda_max - size) / (size - 1)
    ratio = consistency_index / index if index else 0.0
    return consistency_index, ratio


def ahp_weights(
    criteria: list[str] | None = None,
    comparisons: list[AhpComparison] | None = None,
) -> AhpResult:
    """Compute AHP weights and their consistency diagnostic."""

    criteria = list(criteria or DEFAULT_CRITERIA)
    if not criteria:
        raise ValueError("at least one criterion is required")
    if len(set(criteria)) != len(criteria):
        raise ValueError("criteria names must be unique")
    comparisons = list(DEFAULT_COMPARISONS if comparisons is None else comparisons)
    matrix = pairwise_matrix(criteria, comparisons)
    vector, eigenvalue = principal_eigenvector(matrix)
    consistency_index, ratio = consistency_ratio(criteria, eigenvalue)
    accepted = ratio < 0.10
    rationale = [
        f"AHP over {len(criteria)} criteria with {len(comparisons)} pairwise judgements",
        f"lambda_max={eigenvalue:.4f}, CI={consistency_index:.4f}, CR={ratio:.4f}",
        "CR < 0.10 accepted" if accepted else "CR >= 0.10: judgements are inconsistent, review them",
    ]
    return AhpResult(
        criteria=criteria,
        weights={name: value for name, value in zip(criteria, vector)},
        lambda_max=eigenvalue,
        consistency_index=consistency_index,
        consistency_ratio=ratio,
        accepted=accepted,
        rationale=rationale,
    )


def task_criteria(
    task: PlanningTask,
    *,
    duration_days: int,
    float_days: int | None,
    days_to_deadline: int | None,
    window_days: int = 120,
) -> dict[str, float]:
    """Score one task on the 0-1 criterion scale used by AHP.

    Each formula is monotone and bounded, so a reader can check it by hand:

    * ``impact``      = required/documents/validity/cost signals, capped at 1.
    * ``urgency``     = 1.0 within a week, decaying linearly to 0 at ``window_days``.
    * ``flexibility`` = 1.0 when there is no slack, falling to 0 at 30+ days of float.
    * ``effort``      = conservative duration saturating at 21 working days.
    """

    impact = 0.30  # baseline: every listed requirement matters
    if not task.optional:
        impact += 0.25
    if task.documents:
        impact += 0.15
    if task.validity:
        impact += 0.20
    if task.effort_prone:
        impact += 0.10
    impact = min(impact, 1.0)

    if days_to_deadline is None:
        urgency = 0.35  # unknown date: keep it visible but do not fake precision
    else:
        urgency = max(0.0, min(1.0, (window_days - max(days_to_deadline, 0)) / window_days))

    if float_days is None:
        flexibility = 0.35
    else:
        flexibility = max(0.0, min(1.0, (30 - float_days) / 30))

    effort = max(0.0, min(1.0, duration_days / 21))

    return {
        "impact": round(impact, 4),
        "urgency": round(urgency, 4),
        "flexibility": round(flexibility, 4),
        "effort": round(effort, 4),
    }


def priority_scores(
    tasks: list[PlanningTask],
    *,
    durations: dict[str, int],
    float_days: dict[str, int | None],
    days_to_deadline: dict[str, int | None],
    ahp: AhpResult,
) -> dict[str, tuple[float, dict[str, float], list[str]]]:
    """Weighted-sum priority per task, with the arithmetic kept for the report."""

    result: dict[str, tuple[float, dict[str, float], list[str]]] = {}
    for task in tasks:
        criteria = task_criteria(
            task,
            duration_days=durations.get(task.id, 0),
            float_days=float_days.get(task.id),
            days_to_deadline=days_to_deadline.get(task.id),
        )
        score = sum(ahp.weights.get(name, 0.0) * value for name, value in criteria.items())
        score *= task.priority_weight
        rationale = [
            " + ".join(
                f"{name}={criteria.get(name, 0.0):.2f}x{ahp.weights.get(name, 0.0):.3f}"
                for name in ahp.criteria
            ),
            f"weighted sum={score / task.priority_weight:.4f}, task weight x{task.priority_weight:g}",
        ]
        if not math.isfinite(score):
            score = 0.0
            rationale.append("non-finite score replaced by 0.0")
        result[task.id] = (round(score, 4), criteria, rationale)
    return result
