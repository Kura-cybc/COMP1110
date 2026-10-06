"""Plan-time contract for the dependency-modelling and timeline module.

The web-collection module hands over ``models.ResearchResult``: programmes with
free-text facts, ``Step``s, prerequisites, deadline kinds and evidence.  That
schema deliberately does *not* carry scheduling quantities, so this module
declares its own additive contract (``plan_schema_version``) instead of changing
the hand-off that the extraction module and its tests already rely on.

Three ideas are explicit everywhere in this file:

* **state** - ``stated`` / ``inferred`` / ``unknown`` / ``conflicting``.  A task
  may be scheduled from an inferred duration, but never from an unknown date.
* **anchors** - the pipeline never guesses "departure".  Dates are resolved
  against named anchors supplied by the request.
* **provenance** - every task keeps ``source_id`` + ``quote`` + ``retrieved_at``
  so the timeline can show *why* a step is there.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PLANNER_SCHEMA_VERSION = "1.0"

#: Fact status used throughout this module.
TaskState = Literal["stated", "inferred", "unknown", "conflicting"]

#: Per-entry status shown on the timeline (a scheduling verdict, not a fact status).
EntryState = Literal["scheduled", "tight", "at_risk", "infeasible", "unknown"]

#: Where the values of a task came from.
TaskOrigin = Literal["extracted", "curated", "derived"]

#: How a duration estimate was obtained.
DurationBasis = Literal[
    "stated",
    "inferred_similar_case",
    "inferred_convention",
    "unknown",
]

#: How the deadline of a task is expressed.
DeadlineBasis = Literal["fixed", "relative", "none", "unknown"]

#: Anchors a relative deadline may be resolved against.
AnchorKind = Literal[
    "today",
    "submission_deadline",
    "result_release",
    "acceptance_deadline",
    "programme_start",
    "departure",
    "return",
]

#: Machine-readable conflict classes produced by the planner.
ConflictKind = Literal[
    "dependency_cycle",
    "dependency_impossible",
    "deadline_passed",
    "earliest_after_deadline",
    "validity_expires_before_departure",
    "validity_unknown",
    "unknown_deadline",
    "unknown_anchor",
    "relative_deadline_unresolved",
    "missing_duration",
    "unknown_predecessor",
    "task_insufficient_lead_time",
]


class EvidenceLink(BaseModel):
    """A pointer to the page a task field came from."""

    source_id: str
    quote: str = Field(default="", max_length=400)
    url: str | None = None
    retrieved_at: str | None = None


class DurationEstimate(BaseModel):
    """Conservative working time, in calendar days.

    ``max_days`` is what the scheduler uses; it is deliberately pessimistic so
    that the resulting plan is a *latest safe* plan rather than an optimistic one.
    """

    min_days: int = Field(default=0, ge=0)
    max_days: int = Field(default=0, ge=0)
    basis: DurationBasis = "unknown"
    evidence: list[EvidenceLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_order(self):
        if self.max_days < self.min_days:
            raise ValueError("max_days must be >= min_days")
        return self


class ValidityWindow(BaseModel):
    """How long a requirement's evidence stays usable (TimeML-style interval).

    Three ways of expressing the window are supported, and the scheduler reads
    whichever is present - it never assumes a validity length:

    * ``expiry_date`` - the page states when the evidence stops being accepted;
    * ``taken_date`` + ``valid_months`` - the page states the validity period;
    * neither - the window stays ``unknown`` and is reported for manual review.

    ``taken_date`` is the date the evidence is *issued/tested*, so
    ``taken_date + valid_months`` must not fall before ``must_cover``.
    """

    label: str = Field(min_length=1, max_length=120)
    taken_date: _dt.date | None = None
    valid_months: int | None = Field(default=None, gt=0, le=120)
    expiry_date: _dt.date | None = None
    #: The anchor the evidence must still be valid for, e.g. ``departure``.
    must_cover: AnchorKind | None = None
    #: How this window constrains the schedule:
    #:
    #: * ``retention`` (default) - the evidence is produced by this task and must
    #:   still be valid at ``must_cover``.  It becomes a *latest* finish for the
    #:   task, which is how "score expires before departure" is caught.
    #: * ``schedule`` - the task must happen no *earlier* than the validity floor
    #:   (an exam whose score would otherwise expire before it is used).  It is
    #:   reported as review guidance and never turns into an imaginary deadline.
    deadline_kind: Literal["retention", "schedule"] = "retention"
    state: TaskState = "unknown"
    evidence: list[EvidenceLink] = Field(default_factory=list)


class TaskSpec(BaseModel):
    """Additive per-task scheduling facts, keyed by ``models.Step.id``.

    These arrive either from a curated/annotated planning manifest or from a
    deterministic derivation step (section categories, knowledge-base lead
    times).  Nothing here is invented at schedule time.
    """

    task_id: str = Field(min_length=1, max_length=120)
    duration: DurationEstimate = Field(default_factory=DurationEstimate)
    #: Guard time added *before* the deadline for waiting/queueing steps.
    buffer_days: int = Field(default=0, ge=0, le=365)
    #: Conservative guess used when no date can be resolved from the graph.
    fallback_lead_days: int | None = Field(default=None, ge=0, le=730)
    priority_weight: float = Field(default=1.0, gt=0.0, le=100.0)
    effort_prone: bool = False
    validity: list[ValidityWindow] = Field(default_factory=list)
    optional: bool = False


class PlanningTask(BaseModel):
    """A single preparation task node in the temporal dependency graph."""

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    #: Plain-language name for people who are not programmers (shown first in the
    #: user-facing plan).  Falls back to ``title`` when absent.
    title_plain: str | None = Field(default=None, max_length=300)
    #: One sentence telling the student what this step actually involves.
    action_plain: str | None = Field(default=None, max_length=400)
    category: str = Field(default="other", max_length=60)
    programme_id: str = ""
    programme_name: str = ""
    action: str | None = None
    channel: str | None = None
    applies_if: str | None = None
    notes: list[str] = Field(default_factory=list)

    deadline_basis: DeadlineBasis = "unknown"
    #: Concrete date when ``deadline_basis`` is ``fixed``.
    deadline: _dt.date | None = None
    #: Raw deadline text exactly as extracted (kept for display and audit).
    deadline_raw: str | None = None
    #: ``{"anchor": "programme_start", "offset_days": -60}`` when relative.
    deadline_relative: dict[str, Any] | None = None
    deadline_state: TaskState = "unknown"

    deadline_timezone_known: bool = True
    fix_deadline: bool = True

    duration: DurationEstimate = Field(default_factory=DurationEstimate)
    buffer_days: int = Field(default=0, ge=0, le=365)
    fallback_lead_days: int | None = Field(default=None, ge=0, le=730)
    priority_weight: float = Field(default=1.0, gt=0.0, le=100.0)
    effort_prone: bool = False
    optional: bool = False

    validity: list[ValidityWindow] = Field(default_factory=list)

    #: Same-programme predecessor task ids (respects ``models.Step.prerequisites``).
    prerequisites: list[str] = Field(default_factory=list)
    #: Cross-programme / policy edges such as "score must stay valid until departure".
    valid_until: list[str] = Field(default_factory=list)

    documents: list[str] = Field(default_factory=list)
    evidence: list[EvidenceLink] = Field(default_factory=list)

    origin: TaskOrigin = "curated"
    origin_note: str | None = None

    @model_validator(mode="after")
    def _check_deadline(self):
        if self.deadline_basis == "fixed" and self.deadline is None:
            raise ValueError(f"task {self.id!r}: deadline_basis='fixed' requires a date")
        # A relative deadline without a resolvable anchor is *not* a schema error:
        # the research module often reports a trigger phrase instead of an offset,
        # and the planner must report that as ``unknown_anchor`` rather than
        # invent a date or refuse to load the case.
        if self.deadline_basis == "relative" and self.deadline_relative is not None:
            relative = self.deadline_relative
            if not isinstance(relative, dict):
                raise ValueError(
                    f"task {self.id!r}: deadline_relative must be an object with "
                    "'anchor' and 'offset_days'"
                )
        return self


class PlanningAnchor(BaseModel):
    """A named date the whole schedule is measured against."""

    kind: AnchorKind
    label: str = Field(min_length=1, max_length=120)
    anchor_date: _dt.date | None = None
    state: TaskState = "unknown"
    evidence: list[EvidenceLink] = Field(default_factory=list)


class StudentProfile(BaseModel):
    """Minimal, non-sensitive student inputs (no IDs, no documents)."""

    label: str = "anonymous profile"
    year_of_study: int | None = Field(default=None, ge=1, le=8)
    programme_of_study: str = ""
    target_term: str = ""
    language_evidence: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AhpComparison(BaseModel):
    """One pairwise judgement: ``a`` is ``value`` times as important as ``b``."""

    a: str = Field(min_length=1, max_length=40)
    b: str = Field(min_length=1, max_length=40)
    value: float = Field(gt=0.0, le=9.0)


class PlanRequest(BaseModel):
    """Complete input for one plan: tasks + anchors + profile + criteria."""

    plan_schema_version: str = PLANNER_SCHEMA_VERSION
    case_id: str = Field(min_length=1, max_length=120)
    case_label: str = Field(default="", max_length=200)
    programme_id: str = ""
    programme_name: str = ""
    programme_type: Literal["exchange", "summer_school", "winter_school"] = "exchange"
    #: Planning starts from this date (usually the snapshot/retrieval date).
    as_of: _dt.date

    anchors: list[PlanningAnchor] = Field(default_factory=list)
    tasks: list[PlanningTask] = Field(default_factory=list)
    student: StudentProfile = Field(default_factory=StudentProfile)

    #: AHP criteria and pairwise judgements.  Both default to the documented
    #: exchange-preparation set (``planner.ahp.DEFAULT_COMPARISONS``) so a manifest
    #: only has to state the judgements when it disagrees with them.
    criteria: list[str] = Field(
        default_factory=lambda: ["impact", "urgency", "flexibility", "effort"]
    )
    comparisons: list[AhpComparison] | None = None

    #: Optional weekly capacity used to place work (days of effort per week).
    weekly_capacity_days: float | None = Field(default=None, gt=0.0, le=14.0)

    @model_validator(mode="after")
    def _check_request(self):
        ids = [task.id for task in self.tasks]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate task ids: {sorted(duplicates)}")
        # Unknown predecessor ids are *not* a hard error: the planner reports
        # them as ``unknown_predecessor`` conflicts so a broken extraction run
        # still produces a reviewable plan.
        anchor_kinds = [anchor.kind for anchor in self.anchors]
        if len(anchor_kinds) != len(set(anchor_kinds)):
            raise ValueError("duplicate anchor kinds")
        if "departure" not in anchor_kinds:
            raise ValueError("a 'departure' anchor is required: it bounds every latest finish")
        return self


class DependencyEdge(BaseModel):
    """``predecessor`` must finish before ``successor`` may start."""

    predecessor: str
    successor: str
    #: ``prerequisite`` = task order, ``valid_until`` = validity/eligibility hold.
    kind: Literal["prerequisite", "valid_until"] = "prerequisite"
    reason: str = ""


class Conflict(BaseModel):
    kind: ConflictKind
    task_id: str | None = None
    other_task_id: str | None = None
    detail: str
    #: The same problem written for a non-technical reader: what is wrong and what
    #: to do about it.  Built by :mod:`planner.explain`.
    detail_plain: str | None = None
    blocking: bool = True
    evidence: list[EvidenceLink] = Field(default_factory=list)


class PriorityBreakdown(BaseModel):
    """Auditable reasons for a task's position in the work order."""

    score: float
    criteria: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    rationale: list[str] = Field(default_factory=list)


class TimelineEntry(BaseModel):
    """One row of the user-facing plan."""

    task_id: str
    order: int
    title: str
    #: Plain-language name/summary for non-technical readers (see ``PlanningTask``).
    title_plain: str | None = None
    action_plain: str | None = None
    category: str
    programme_id: str = ""
    programme_name: str = ""

    earliest_start: _dt.date | None = None
    latest_start: _dt.date | None = None
    recommended_start: _dt.date | None = None
    deadline: _dt.date | None = None
    deadline_source: str = "unknown"
    #: ``deadline_source`` written as a sentence for non-technical readers.
    deadline_source_plain: str = ""
    duration_days: int = 0
    buffer_days: int = 0
    float_days: int | None = None
    #: True only when the task has **zero** slack: slipping it moves the finish.
    on_critical_path: bool = False
    #: True when the task lies on the chain reported in ``Plan.critical_path``
    #: (which falls back to the longest dependency chain when no task has zero
    #: slack).  Kept separate so "critical" is never over-claimed.
    on_driver_chain: bool = False

    state: EntryState = "unknown"
    #: ``state`` in everyday words, e.g. "时间比较紧".
    state_plain: str = ""
    blocking_conflicts: list[str] = Field(default_factory=list)
    #: The blocking/review problems written for non-technical readers.
    blocking_plain: list[str] = Field(default_factory=list)
    warnings_plain: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    documents: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    action: str | None = None
    channel: str | None = None
    applies_if: str | None = None
    sources: list[EvidenceLink] = Field(default_factory=list)
    priority: PriorityBreakdown | None = None


class TimelineWeek(BaseModel):
    week_start: _dt.date
    week_end: _dt.date
    items: list[str] = Field(default_factory=list)
    effort_days: int = 0


class Plan(BaseModel):
    """Output contract of this module (hand-off to the integration module)."""

    plan_schema_version: str = PLANNER_SCHEMA_VERSION
    case_id: str
    case_label: str = ""
    programme_id: str = ""
    programme_name: str = ""
    as_of: _dt.date
    anchors: list[PlanningAnchor] = Field(default_factory=list)

    entries: list[TimelineEntry] = Field(default_factory=list)
    weeks: list[TimelineWeek] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    critical_path: list[str] = Field(default_factory=list)
    #: How ``critical_path`` was derived: ``zero_slack`` or
    #: ``longest_dependency_chain`` (when nothing has zero slack).
    critical_path_basis: Literal["zero_slack", "longest_dependency_chain", "none"] = "none"
    conflicts: list[Conflict] = Field(default_factory=list)

    ahp: dict[str, Any] = Field(default_factory=dict)
    feasibility: Literal["feasible", "at_risk", "infeasible", "unknown"] = "unknown"
    unscheduled: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    #: ``notes`` rewritten for non-technical readers.
    notes_plain: list[str] = Field(default_factory=list)


def load_plan_request(path: str | Path) -> PlanRequest:
    """Load and validate a planning manifest (JSON)."""

    text = Path(path).read_text(encoding="utf-8")
    return PlanRequest.model_validate(json.loads(text))


def add_months(start: _dt.date, months: int) -> _dt.date:
    """Calendar-correct month arithmetic (31 Jan + 1 month = 28/29 Feb)."""

    year = start.year + (start.month - 1 + months) // 12
    month = (start.month - 1 + months) % 12 + 1
    day = min(start.day, days_in_month(year, month))
    return _dt.date(year, month, day)


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day


def task_specs_from_request(request: PlanRequest) -> dict[str, TaskSpec]:
    """Extract the additive scheduling facts so they can be merged with steps."""

    return {
        task.id: TaskSpec(
            task_id=task.id,
            duration=task.duration,
            buffer_days=task.buffer_days,
            fallback_lead_days=task.fallback_lead_days,
            priority_weight=task.priority_weight,
            effort_prone=task.effort_prone,
            validity=task.validity,
            optional=task.optional,
        )
        for task in request.tasks
    }


def adapt_research_result(
    result: Any,
    *,
    task_specs: dict[str, TaskSpec] | None = None,
    anchors: list[PlanningAnchor] | None = None,
    profile: StudentProfile | None = None,
    as_of: _dt.date | None = None,
    case_id: str | None = None,
) -> PlanRequest:
    """Bridge ``models.ResearchResult`` into a :class:`PlanRequest`.

    ``task_specs`` supplies the scheduling quantities the research schema lacks
    (duration, buffer, validity).  Tasks without a spec still appear in the plan,
    flagged as ``missing_duration`` rather than being silently dropped.
    """

    specs = task_specs or {}
    result_anchors = list(anchors or [])
    tasks: list[PlanningTask] = []
    for programme in getattr(result, "programmes", []):
        for step in programme.steps:
            spec = specs.get(step.id)
            task = PlanningTask(
                id=step.id,
                title=step.title,
                category=step.category,
                programme_id=programme.id,
                programme_name=programme.name,
                action=step.action.value,
                channel=step.channel.value,
                applies_if=step.applies_if,
                notes=list(step.notes),
                deadline_basis=_deadline_basis(step.deadline.kind, step.deadline.date),
                deadline=_safe_date(step.deadline.date),
                deadline_raw=step.deadline.raw or step.deadline.trigger,
                deadline_state=_map_state(step.deadline.state),
                prerequisites=list(step.prerequisites),
                documents=[doc.name for doc in step.documents],
                evidence=[
                    EvidenceLink(source_id=item.source_id, quote=item.quote)
                    for item in (step.action.evidence + step.deadline.evidence)
                ],
                origin="extracted",
                origin_note=f"models.Step from programme {programme.id}",
            )
            if spec is not None:
                task.duration = spec.duration
                task.buffer_days = spec.buffer_days
                task.fallback_lead_days = spec.fallback_lead_days
                task.priority_weight = spec.priority_weight
                task.effort_prone = spec.effort_prone
                task.validity = spec.validity
                task.optional = spec.optional
            tasks.append(task)

    request = PlanRequest(
        case_id=case_id or f"research_{getattr(result, 'provider', 'snapshot')}",
        case_label=getattr(result, "provider", "research result"),
        as_of=as_of or _safe_date(getattr(result, "generated_at", None)) or _dt.date.today(),
        anchors=result_anchors,
        tasks=tasks,
        student=profile or StudentProfile(),
    )
    if not any(anchor.kind == "departure" for anchor in request.anchors):
        raise ValueError(
            "adapt_research_result needs a 'departure' anchor; "
            "the research schema has no departure field"
        )
    return request


def _deadline_basis(kind: str, value: str | None) -> DeadlineBasis:
    if kind == "fixed" and value:
        return "fixed" if _safe_date(value) else "unknown"
    if kind == "relative":
        return "relative"
    if kind in {"none", "rolling", "window"}:
        return "none"
    return "unknown"


def _map_state(state: str) -> TaskState:
    if state == "verified":
        return "stated"
    if state in {"needs_review", "historical"}:
        return "inferred" if state == "historical" else "unknown"
    if state == "conflicting":
        return "conflicting"
    return "unknown"


def parse_iso_date(value: Any) -> _dt.date | None:
    """Best-effort ISO date parse; anything unparseable stays ``None``.

    ``datetime`` inputs (``ResearchResult.generated_at``) are reduced to their
    calendar date so both types can flow into one contract.
    """

    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


#: Backwards-compatible private alias used inside this module.
_safe_date = parse_iso_date
