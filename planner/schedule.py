"""Reverse scheduling and feasibility checking on the dependency graph.

The algorithm is a conservative critical-path (CPM) pass with three additions
that exchange preparation needs:

1. **Anchors instead of guesses.**  A relative deadline such as "60 days before
   the programme starts" is resolved against a named anchor.  If the anchor has
   no date, the task keeps an ``unknown`` state instead of receiving an invented
   date.
2. **Validity windows as real constraints.**  "The language score must still be
   valid at departure" becomes a *latest finish* for the task that produces the
   score, so an expiring score is caught while the plan is still changeable.
3. **Today as a constraint, not as a silence.**  A task whose latest start is
   already behind us is reported as ``earliest_after_deadline`` (infeasible),
   never quietly scheduled in the past.

Passes, in order: deadline resolution, forward (earliest start/finish), backward
(latest finish/start), slack and feasibility, validity checks, structural checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from planner.graph import DependencyGraph, build_graph
from planner.schema import (
    Conflict,
    PlanningAnchor,
    PlanningTask,
    PlanRequest,
    StudentProfile,
    ValidityWindow,
    add_months,
)


@dataclass
class ScheduleResult:
    graph: DependencyGraph
    anchors: dict[str, PlanningAnchor] = field(default_factory=dict)
    horizon: date | None = None
    period_days: int = 0
    durations: dict[str, int] = field(default_factory=dict)
    deadline: dict[str, date | None] = field(default_factory=dict)
    deadline_source: dict[str, str] = field(default_factory=dict)
    validity_deadline: dict[str, date] = field(default_factory=dict)
    earliest_start: dict[str, date | None] = field(default_factory=dict)
    earliest_finish: dict[str, date | None] = field(default_factory=dict)
    latest_finish: dict[str, date | None] = field(default_factory=dict)
    latest_start: dict[str, date | None] = field(default_factory=dict)
    float_days: dict[str, int | None] = field(default_factory=dict)
    days_to_deadline: dict[str, int | None] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    task_state: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def blocking_conflicts(self) -> list[Conflict]:
        return [conflict for conflict in self.conflicts if conflict.blocking]

    @property
    def feasibility(self) -> str:
        if not self.graph.is_acyclic:
            return "infeasible"
        if any(conflict.blocking for conflict in self.conflicts):
            return "infeasible"
        if any(
            conflict.kind in {"validity_unknown", "unknown_deadline", "missing_duration"}
            for conflict in self.conflicts
        ):
            return "at_risk"
        if any(slack is not None and 0 <= slack <= 7 for slack in self.float_days.values()):
            return "at_risk"
        return "feasible"


def schedule(request: PlanRequest, graph: DependencyGraph | None = None) -> ScheduleResult:
    """Run the deadline resolution and scheduling passes over ``request``."""

    graph = graph or build_graph(request.tasks)
    anchors = {anchor.kind: anchor for anchor in request.anchors}
    horizon = _horizon(request, anchors)
    result = ScheduleResult(graph=graph, anchors=anchors, horizon=horizon)
    result.period_days = (horizon - request.as_of).days if horizon else 0
    if horizon is not None and horizon < request.as_of:
        result.notes.append(
            f"planning horizon {horizon.isoformat()} is before 'as_of' "
            f"{request.as_of.isoformat()}; every latest-start value is in the past"
        )

    for task in request.tasks:
        result.durations[task.id] = task.duration.max_days
        # Every task gets an explicit entry (possibly ``None``) so callers never
        # have to distinguish "no deadline" from "not computed".
        result.deadline[task.id] = None
        _resolve_deadline(task, result, anchors)

    _forward_pass(request, result)
    _backward_pass(request, result)
    _slack_and_feasibility(request, result)
    _validity_checks(request, result)
    _structural_conflicts(request, result)
    return result


# --------------------------------------------------------------------------- #
# validity windows
# --------------------------------------------------------------------------- #
def validity_expiry(window: ValidityWindow) -> date | None:
    """When does this piece of evidence stop being acceptable?

    ``None`` means the window cannot be computed, which is reported rather than
    guessed.  ``taken_date`` + ``valid_months`` uses calendar arithmetic.
    """

    if window.expiry_date is not None:
        return window.expiry_date
    if window.taken_date is not None and window.valid_months is not None:
        return add_months(window.taken_date, window.valid_months)
    return None


def retention_deadline(
    task: PlanningTask,
    anchors: dict[str, PlanningAnchor],
) -> tuple[date | None, str | None]:
    """When does already-issued evidence stop being usable?

    This is a **validation** quantity, not a scheduling bound: the task that
    produces the evidence is scheduled by its own deadline and its successors'
    latest starts, while this value answers "is the evidence still valid at the
    anchor?".  Mixing the two is exactly the modelling error this module exists
    to prevent: an exam taken *later* is valid *longer*, so a retention window
    can never impose an upper bound on when the exam happens.
    """

    latest: date | None = None
    source: str | None = None
    for window in task.validity:
        if window.deadline_kind != "retention":
            continue
        anchor = anchors.get(window.must_cover) if window.must_cover else None
        expiry = validity_expiry(window)
        if expiry is None:
            continue
        if latest is None or expiry < latest:
            latest = expiry
            target = (
                f"{anchor.label} ({anchor.anchor_date.isoformat()})"
                if anchor is not None and anchor.anchor_date is not None
                else f"anchor '{window.must_cover}'"
            )
            source = f"{window.label} must still be valid at {target}"
    return latest, source


# --------------------------------------------------------------------------- #
# deadline resolution
# --------------------------------------------------------------------------- #
def _horizon(request: PlanRequest, anchors: dict[str, PlanningAnchor]) -> date | None:
    """The hard end of the planning window.

    Preparation must physically finish before departure, so ``departure`` is the
    horizon whenever it has a date; otherwise the first dated anchor wins.
    """

    for kind in ("departure", "programme_start", "return", "result_release"):
        anchor = anchors.get(kind)
        if anchor is not None and anchor.anchor_date is not None:
            return anchor.anchor_date
    for anchor in request.anchors:
        if anchor.anchor_date is not None:
            return anchor.anchor_date
    return None


def _resolve_deadline(
    task: PlanningTask,
    result: ScheduleResult,
    anchors: dict[str, PlanningAnchor],
) -> None:
    """Decide the task's own deadline: explicit, anchored, or explicitly unknown."""

    if task.deadline_basis == "fixed" and task.deadline is not None:
        result.deadline[task.id] = task.deadline
        result.deadline_source[task.id] = (
            "fixed deadline from source"
            if task.fix_deadline
            else "latest acceptable date from source"
        )
        return

    if task.deadline_basis == "relative":
        relative = task.deadline_relative or {}
        anchor_kind = str(relative.get("anchor", ""))
        offset = int(relative.get("offset_days", 0))
        if not anchor_kind:
            # The research module reports a trigger phrase ("after the host
            # offer") without an offset.  That is a review item, never a date.
            result.conflicts.append(
                Conflict(
                    kind="relative_deadline_unresolved",
                    task_id=task.id,
                    detail=(
                        f"relative deadline '{task.deadline_raw or task.deadline_state}' "
                        "has no anchor and offset, so it cannot be resolved to a date; "
                        "confirm the trigger and its lead time with the official page"
                    ),
                    evidence=task.evidence[:1],
                )
            )
            result.deadline_source[task.id] = "relative deadline without an anchor"
            return
        anchor = anchors.get(anchor_kind)  # type: ignore[arg-type]
        if anchor is None:
            result.conflicts.append(
                Conflict(
                    kind="unknown_anchor",
                    task_id=task.id,
                    detail=(
                        f"relative deadline refers to anchor '{anchor_kind}', "
                        "which this request does not define"
                    ),
                )
            )
            result.deadline_source[task.id] = f"unknown anchor '{anchor_kind}'"
            return
        if anchor.anchor_date is None:
            result.conflicts.append(
                Conflict(
                    kind="unknown_anchor",
                    task_id=task.id,
                    detail=(
                        f"anchor '{anchor.label}' ({anchor_kind}) has no grounded date, so "
                        f"'{task.deadline_raw or 'the relative deadline'}' cannot be resolved"
                    ),
                    evidence=anchor.evidence,
                )
            )
            result.deadline_source[task.id] = f"anchor '{anchor_kind}' undated"
            return
        resolved = anchor.anchor_date + timedelta(days=offset)
        result.deadline[task.id] = resolved
        result.deadline_source[task.id] = (
            f"{offset:+d} days from {anchor.label} ({anchor.anchor_date.isoformat()})"
        )
        return

    if task.deadline_basis == "none":
        result.deadline_source[task.id] = "no fixed deadline stated"
        return

    result.deadline_source[task.id] = "deadline unknown"
    result.conflicts.append(
        Conflict(
            kind="unknown_deadline",
            task_id=task.id,
            blocking=False,
            detail=(
                "no grounded deadline: the plan places this task after its prerequisites "
                "and before the horizon, and flags it for manual confirmation"
            ),
            evidence=task.evidence[:1],
        )
    )
    if task.fallback_lead_days is not None:
        result.notes.append(
            f"task {task.id}: the fallback lead time of {task.fallback_lead_days} day(s) is "
            "used only to order work, never as a stated deadline"
        )


# --------------------------------------------------------------------------- #
# forward / backward passes
# --------------------------------------------------------------------------- #
def _forward_pass(request: PlanRequest, result: ScheduleResult) -> None:
    for task_id in result.graph.topological_order:
        start = request.as_of
        for predecessor in result.graph.prerequisite_predecessors(task_id):
            finish = result.earliest_finish.get(predecessor)
            if finish is not None and finish > start:
                start = finish
        result.earliest_start[task_id] = start
        result.earliest_finish[task_id] = start + timedelta(days=result.durations.get(task_id, 0))


def _backward_pass(request: PlanRequest, result: ScheduleResult) -> None:
    for task_id in reversed(result.graph.topological_order):
        task = result.graph.tasks[task_id]
        bounds: list[tuple[date, str]] = []

        deadline = result.deadline.get(task_id)
        if deadline is not None:
            bounds.append((deadline, result.deadline_source.get(task_id, "stated deadline")))

        for successor in result.graph.prerequisite_successors(task_id):
            successor_start = result.latest_start.get(successor)
            if successor_start is not None:
                bounds.append((successor_start, f"latest start of successor {successor}"))

        retention, retention_source = retention_deadline(task, result.anchors)
        if retention is not None:
            result.validity_deadline[task_id] = retention

        if bounds:
            finish, source = min(bounds, key=lambda item: item[0])
        else:
            finish = result.horizon
            source = "planning horizon (departure)" if finish else "unbounded"

        result.latest_finish[task_id] = finish
        result.latest_start[task_id] = (
            None
            if finish is None
            else finish - timedelta(days=result.durations.get(task_id, 0) + task.buffer_days)
        )

        own = result.deadline_source.get(task_id, "derived")
        if source != own:
            result.deadline_source[task_id] = f"{own}; limited by {source}"


def _slack_and_feasibility(request: PlanRequest, result: ScheduleResult) -> None:
    for task_id in result.graph.topological_order:
        task = result.graph.tasks[task_id]
        earliest = result.earliest_start.get(task_id)
        latest = result.latest_start.get(task_id)

        if earliest is None or latest is None:
            result.float_days[task_id] = None
            result.days_to_deadline[task_id] = None
            result.task_state[task_id] = "unknown"
            continue

        result.float_days[task_id] = (latest - earliest).days
        result.days_to_deadline[task_id] = (latest - request.as_of).days

        if latest < request.as_of:
            result.task_state[task_id] = "infeasible"
            result.conflicts.append(
                Conflict(
                    kind="earliest_after_deadline",
                    task_id=task.id,
                    detail=(
                        f"latest start {latest.isoformat()} is already "
                        f"{(request.as_of - latest).days} day(s) before 'as_of' "
                        f"{request.as_of.isoformat()}; constrained by "
                        f"{result.deadline_source.get(task_id, 'unknown')}"
                    ),
                    evidence=task.evidence[:1],
                )
            )
            continue

        if earliest > latest:
            result.task_state[task_id] = "infeasible"
            result.conflicts.append(
                Conflict(
                    kind="earliest_after_deadline",
                    task_id=task.id,
                    detail=(
                        f"earliest possible start {earliest.isoformat()} is after the latest "
                        f"allowed start {latest.isoformat()} ({(earliest - latest).days} day(s) "
                        "short): the prerequisites alone fill the whole window"
                    ),
                    evidence=task.evidence[:1],
                )
            )
            continue

        if task.duration.basis == "unknown" and task.duration.max_days == 0:
            result.task_state[task_id] = "at_risk"
            result.conflicts.append(
                Conflict(
                    kind="missing_duration",
                    task_id=task.id,
                    blocking=False,
                    detail=(
                        "no grounded working time for this task: it is placed by dependency "
                        "order only, so its dates are indicative"
                    ),
                )
            )
            continue

        result.task_state[task_id] = "ok" if (result.float_days[task_id] or 0) > 7 else "tight"


def _validity_checks(request: PlanRequest, result: ScheduleResult) -> None:
    """Surface "the score expires before it is used" at plan time."""

    for task in request.tasks:
        for window in task.validity:
            must_cover = window.must_cover
            anchor = result.anchors.get(must_cover) if must_cover else None
            label = f"{task.id}/{window.label}"

            if must_cover is not None and anchor is None:
                result.conflicts.append(
                    Conflict(
                        kind="unknown_anchor",
                        task_id=task.id,
                        detail=(
                            f"{label}: the validity window must cover anchor '{must_cover}', "
                            "which this request does not define"
                        ),
                    )
                )
                continue

            if anchor is None or anchor.anchor_date is None:
                continue

            if window.deadline_kind == "schedule":
                floor = (
                    add_months(anchor.anchor_date, -window.valid_months)
                    if window.valid_months
                    else None
                )
                if floor is not None and floor <= request.as_of:
                    result.conflicts.append(
                        Conflict(
                            kind="task_insufficient_lead_time",
                            task_id=task.id,
                            blocking=False,
                            detail=(
                                f"{label}: to still be valid at {anchor.label} "
                                f"({anchor.anchor_date.isoformat()}) the task should already have "
                                f"happened by {floor.isoformat()}; confirm whether the evidence "
                                "must be re-taken before it is submitted"
                            ),
                            evidence=window.evidence[:1],
                        )
                    )
                elif floor is not None:
                    result.notes.append(
                        f"{label}: earliest useful date is {floor.isoformat()} so the evidence "
                        f"is still valid at {anchor.label} ({anchor.anchor_date.isoformat()})"
                    )
                continue

            expiry = validity_expiry(window)
            if expiry is None:
                result.conflicts.append(
                    Conflict(
                        kind="validity_unknown",
                        task_id=task.id,
                        blocking=False,
                        detail=(
                            f"{label} has neither an expiry date nor a stated validity period, so "
                            f"it cannot be checked against {anchor.label} "
                            f"({anchor.anchor_date.isoformat()}); verify it on the official page"
                        ),
                        evidence=window.evidence[:1],
                    )
                )
                continue

            if expiry >= anchor.anchor_date:
                result.notes.append(
                    f"{label}: valid until {expiry.isoformat()}, which covers {anchor.label} "
                    f"({anchor.anchor_date.isoformat()})"
                )
                continue

            latest_issue = (
                add_months(anchor.anchor_date, -window.valid_months) if window.valid_months else None
            )
            detail = (
                f"{label} expires {expiry.isoformat()} but must remain valid until "
                f"{anchor.label} ({anchor.anchor_date.isoformat()}): "
                f"{(anchor.anchor_date - expiry).days} day(s) short"
            )
            if latest_issue is not None:
                detail += (
                    f"; evidence issued on {window.taken_date.isoformat()} is no longer usable, "
                    f"re-take or re-issue it no later than {latest_issue.isoformat()}"
                    if window.taken_date
                    else f"; the evidence must be issued no later than {latest_issue.isoformat()}"
                )
            result.conflicts.append(
                Conflict(
                    kind="validity_expires_before_departure",
                    task_id=task.id,
                    detail=detail,
                    evidence=window.evidence or task.evidence[:1],
                )
            )
            result.task_state[task.id] = "infeasible"


def _structural_conflicts(request: PlanRequest, result: ScheduleResult) -> None:
    for cycle in result.graph.cycles:
        result.conflicts.append(
            Conflict(
                kind="dependency_cycle",
                task_id=cycle[0],
                other_task_id=cycle[-1],
                detail=(
                    "circular prerequisite chain: "
                    + " -> ".join([*cycle, cycle[0]])
                    + "; the plan cannot be ordered until one edge is removed"
                ),
            )
        )
    for successor, missing in sorted(result.graph.dangling_predecessors.items()):
        result.conflicts.append(
            Conflict(
                kind="unknown_predecessor",
                task_id=successor,
                detail=(
                    f"prerequisite id(s) {', '.join(sorted(missing))} do not exist in this plan; "
                    "they were ignored, so the ordering may be incomplete"
                ),
            )
        )

    # Two orderings that are individually legal but jointly impossible: a
    # successor that must start before its predecessor can possibly finish.
    for edge in result.graph.edges:
        if edge.kind != "prerequisite":
            continue
        predecessor_finish = result.earliest_finish.get(edge.predecessor)
        successor_deadline = result.deadline.get(edge.successor)
        if predecessor_finish is None or successor_deadline is None:
            continue
        if predecessor_finish > successor_deadline:
            result.conflicts.append(
                Conflict(
                    kind="dependency_impossible",
                    task_id=edge.successor,
                    other_task_id=edge.predecessor,
                    detail=(
                        f"{edge.successor} is due {successor_deadline.isoformat()} but its "
                        f"prerequisite {edge.predecessor} cannot finish before "
                        f"{predecessor_finish.isoformat()}"
                    ),
                )
            )

    for edge in result.graph.validity_edges():
        holder, requirement = edge.predecessor, edge.successor
        holder_latest = result.latest_finish.get(holder)
        requirement_deadline = result.deadline.get(requirement)
        detail = (
            f"{holder} must still be valid for {requirement}: keep the evidence inside its "
            "validity window"
        )
        if holder_latest is not None and requirement_deadline is not None:
            detail += (
                f" (usable until {holder_latest.isoformat()}, needed by "
                f"{requirement_deadline.isoformat()})"
            )
        result.notes.append(detail)


def profile_summary(profile: StudentProfile) -> str:
    """One-line, non-sensitive profile description for the plan header."""

    parts = [profile.label]
    if profile.year_of_study:
        parts.append(f"Year {profile.year_of_study}")
    if profile.programme_of_study:
        parts.append(profile.programme_of_study)
    if profile.target_term:
        parts.append(profile.target_term)
    return " · ".join(parts)
