"""The user-facing plan: work order, dated windows and source links.

This module is where the graph, the schedule and the AHP weights become an
answer to the project's central question - an ordered, dated, checkable
preparation plan.  Three rules are enforced here:

1. **Order is legal.**  The work order is a topological order, produced greedily
   by always taking the highest-priority *ready* task, so priority never breaks a
   prerequisite.
2. **Dates are windows, not wishes.**  Every entry carries the earliest legal
   start, the latest safe start and the resulting slack.  Work with slack is
   placed early according to priority, and the latest start stays visible as the
   real deadline.
3. **Nothing is hidden.**  Conflicts, unknowns and review notes travel with the
   plan and are also rendered as Markdown for the report appendix.
"""

from __future__ import annotations

from datetime import date, timedelta

from planner.ahp import AhpResult, ahp_weights, priority_scores
from planner.explain import plain_conflict, plain_deadline_source, plain_note, plain_state
from planner.graph import DependencyGraph, build_graph
from planner.schedule import ScheduleResult, profile_summary, schedule
from planner.schema import (
    PLANNER_SCHEMA_VERSION,
    Conflict,
    EvidenceLink,
    Plan,
    PlanRequest,
    PlanningTask,
    PriorityBreakdown,
    TimelineEntry,
    TimelineWeek,
)

#: States that mean the task cannot be executed as planned.
BLOCKING_STATES = {"infeasible"}


def build_plan(request: PlanRequest) -> Plan:
    """Produce the complete plan for one request."""

    graph = build_graph(request.tasks)
    result = schedule(request, graph)
    ahp = ahp_weights(request.criteria, request.comparisons)
    scores = priority_scores(
        request.tasks,
        durations=result.durations,
        float_days=result.float_days,
        days_to_deadline=result.days_to_deadline,
        ahp=ahp,
    )
    work_order = priority_order(
        graph, {task_id: score for task_id, (score, _, _) in scores.items()}
    )
    ordered_tasks = [graph.tasks[task_id] for task_id in work_order]

    entries = [
        _entry(index, task, result, scores.get(task.id), ahp)
        for index, task in enumerate(ordered_tasks, start=1)
    ]

    conflicts = _rank_conflicts(result.conflicts)
    critical_path = graph.critical_path(result.float_days, result.durations)
    critical_path_basis = "zero_slack"
    if not critical_path:
        # No task has exactly zero slack (everything still has room), so the
        # schedule is driven by the longest dependency chain instead.  The two
        # notions are reported separately so "critical" is never over-claimed.
        critical_path = graph.longest_chain(result.durations)
        critical_path_basis = "longest_dependency_chain" if critical_path else "none"
    driver_chain = set(critical_path)
    for entry in entries:
        entry.on_critical_path = entry.float_days == 0
        entry.on_driver_chain = entry.task_id in driver_chain

    # Plain-language wording for readers who are not programmers.  The precise
    # JSON fields above stay untouched; these are presentations of the same facts.
    titles = {task.id: (task.title_plain or task.title) for task in request.tasks}
    for conflict in conflicts:
        conflict.detail_plain = plain_conflict(conflict, titles)
    for entry in entries:
        entry.state_plain = plain_state(entry.state)
        entry.deadline_source_plain = plain_deadline_source(entry.deadline_source)
        entry.blocking_plain = [
            plain_conflict(conflict, titles)
            for conflict in conflicts
            if conflict.task_id == entry.task_id and conflict.blocking
        ]
        entry.warnings_plain = [
            plain_conflict(conflict, titles)
            for conflict in conflicts
            if conflict.task_id == entry.task_id and not conflict.blocking
        ]

    notes = [
        f"profile: {profile_summary(request.student)}",
        f"planning horizon: {result.horizon.isoformat()}"
        if result.horizon
        else "no dated horizon",
        f"critical path basis: {critical_path_basis}",
        *result.notes,
    ]

    plan = Plan(
        plan_schema_version=PLANNER_SCHEMA_VERSION,
        case_id=request.case_id,
        case_label=request.case_label,
        programme_id=request.programme_id,
        programme_name=request.programme_name,
        as_of=request.as_of,
        anchors=request.anchors,
        entries=entries,
        weeks=_weeks(entries, request),
        edges=graph.edges,
        critical_path=critical_path,
        critical_path_basis=critical_path_basis,
        conflicts=conflicts,
        ahp=ahp.as_dict(),
        feasibility=result.feasibility,
        unscheduled=list(result.graph.cycles[0]) if result.graph.cycles else [],
        stats=_stats(request, entries, conflicts, critical_path, ahp),
        notes=notes,
        notes_plain=_plain_notes(notes, titles),
    )
    return plan


def _plain_notes(notes: list[str], titles: dict[str, str]) -> list[str]:
    """Translate the audit-trail notes, substituting readable task names."""

    out: list[str] = []
    for note in notes:
        text = plain_note(note)
        for task_id, title in titles.items():
            text = text.replace(task_id, title)
        out.append(text)
    return out


# --------------------------------------------------------------------------- #
# work ordering
# --------------------------------------------------------------------------- #
def priority_order(graph: DependencyGraph, scores: dict[str, float]) -> list[str]:
    """Greedy topological order: highest priority among currently ready tasks.

    This is the bridge between AHP and the dependency graph.  A task is *ready*
    only when every prerequisite has already been ordered, so the output is
    always a legal sequence; priority decides only which legal task comes next.
    Ordering is deterministic (ties break on task id) so repeated runs on the
    same case produce identical plans.
    """

    ready_blockers = {
        task_id: set(graph.prerequisite_predecessors(task_id)) for task_id in graph.tasks
    }
    ordered: list[str] = []
    while ready_blockers:
        ready = [task_id for task_id, blockers in ready_blockers.items() if not blockers]
        if not ready:
            # Cyclic remainder: keep the plan reviewable instead of failing.
            ordered.extend(sorted(ready_blockers))
            break
        ready.sort(key=lambda task_id: (-scores.get(task_id, 0.0), task_id))
        chosen = ready[0]
        ordered.append(chosen)
        ready_blockers.pop(chosen)
        for blockers in ready_blockers.values():
            blockers.discard(chosen)
    return ordered


# --------------------------------------------------------------------------- #
# entries
# --------------------------------------------------------------------------- #
def _entry(
    order: int,
    task: PlanningTask,
    result: ScheduleResult,
    score: tuple[float, dict[str, float], list[str]] | None,
    ahp: AhpResult,
) -> TimelineEntry:
    earliest = result.earliest_start.get(task.id)
    latest = result.latest_start.get(task.id)
    float_days = result.float_days.get(task.id)
    state = result.task_state.get(task.id, "unknown")

    recommended = _recommended_start(
        earliest, latest, float_days, score[0] if score else 0.5
    )

    blocking = [
        conflict.detail
        for conflict in result.conflicts
        if conflict.task_id == task.id and conflict.blocking
    ]
    warnings = [
        conflict.detail
        for conflict in result.conflicts
        if conflict.task_id == task.id and not conflict.blocking
    ]
    if state in BLOCKING_STATES and not blocking:
        blocking.append("task state is infeasible")

    breakdown = None
    if score is not None:
        breakdown = PriorityBreakdown(
            score=score[0],
            criteria=score[1],
            weights={name: round(value, 4) for name, value in ahp.weights.items()},
            rationale=score[2],
        )

    return TimelineEntry(
        task_id=task.id,
        order=order,
        title=task.title,
        title_plain=task.title_plain or task.title,
        action_plain=task.action_plain,
        category=task.category,
        programme_id=task.programme_id,
        programme_name=task.programme_name,
        earliest_start=earliest,
        latest_start=latest,
        recommended_start=recommended,
        deadline=result.deadline.get(task.id),
        deadline_source=result.deadline_source.get(task.id, "unknown"),
        duration_days=result.durations.get(task.id, 0),
        buffer_days=task.buffer_days,
        float_days=float_days,
        state=_entry_state(task, state, blocking, warnings),
        blocking_conflicts=blocking,
        warnings=warnings,
        documents=list(task.documents),
        prerequisites=list(task.prerequisites),
        action=task.action,
        channel=task.channel,
        applies_if=task.applies_if,
        sources=_sources(task),
        priority=breakdown,
    )


def _recommended_start(
    earliest: date | None, latest: date | None, float_days: int | None, score: float
) -> date | None:
    """Place work inside its legal window, biased early by priority.

    A critical task (no slack) starts as soon as it is legal.  A task with slack
    is pulled forward proportionally to its AHP score, but never past its latest
    safe start, so the recommendation can always be executed.
    """

    if earliest is None:
        return None
    if latest is None or float_days is None or float_days <= 0:
        return earliest
    bounded = min(max(score, 0.0), 1.5) / 1.5
    shift = round(float_days * (1.0 - bounded))
    return min(earliest + timedelta(days=shift), latest)


def _entry_state(
    task: PlanningTask, state: str, blocking: list[str], warnings: list[str]
) -> str:
    if blocking:
        return "infeasible"
    if state == "at_risk" or warnings:
        return "at_risk"
    if task.deadline_basis == "unknown" and task.deadline_state == "unknown":
        return "unknown"
    return "scheduled"


def _sources(task: PlanningTask) -> list[EvidenceLink]:
    seen: set[tuple[str, str]] = set()
    sources: list[EvidenceLink] = []
    for link in [*task.evidence, *[item for window in task.validity for item in window.evidence]]:
        key = (link.source_id, link.quote)
        if key in seen:
            continue
        seen.add(key)
        sources.append(link)
    return sources


# --------------------------------------------------------------------------- #
# weeks, stats, conflicts
# --------------------------------------------------------------------------- #
def _weeks(entries: list[TimelineEntry], request: PlanRequest) -> list[TimelineWeek]:
    starts = [entry.recommended_start for entry in entries if entry.recommended_start]
    if not starts:
        return []
    last_day = max(
        [*starts, *[entry.deadline for entry in entries if entry.deadline]]
    )
    week_start = starts[0] - timedelta(days=starts[0].weekday())  # Monday
    weeks: list[TimelineWeek] = []
    while week_start <= last_day:
        weeks.append(
            TimelineWeek(week_start=week_start, week_end=week_start + timedelta(days=6))
        )
        week_start += timedelta(days=7)

    for entry in entries:
        if entry.recommended_start is None:
            continue
        index = (entry.recommended_start - weeks[0].week_start).days // 7
        if 0 <= index < len(weeks):
            weeks[index].items.append(entry.task_id)
            weeks[index].effort_days += entry.duration_days

    if request.weekly_capacity_days is not None:
        for week in weeks:
            if week.effort_days > request.weekly_capacity_days:
                for task_id in week.items:
                    for entry in entries:
                        if entry.task_id == task_id:
                            entry.warnings.append(
                                f"week {week.week_start.isoformat()} carries "
                                f"{week.effort_days} day(s) of estimated effort against a "
                                f"capacity of {request.weekly_capacity_days:g}; consider "
                                "moving low-priority work earlier"
                            )
    return weeks


def _rank_conflicts(conflicts: list[Conflict]) -> list[Conflict]:
    order = {
        "dependency_cycle": 0,
        "dependency_impossible": 1,
        "validity_expires_before_departure": 2,
        "earliest_after_deadline": 3,
        "deadline_passed": 4,
        "task_insufficient_lead_time": 5,
        "unknown_anchor": 6,
        "relative_deadline_unresolved": 7,
        "unknown_predecessor": 8,
        "unknown_deadline": 9,
        "missing_duration": 10,
        "validity_unknown": 11,
    }
    return sorted(
        conflicts,
        key=lambda conflict: (
            0 if conflict.blocking else 1,
            order.get(conflict.kind, 99),
            conflict.task_id or "",
        ),
    )


def _stats(
    request: PlanRequest,
    entries: list[TimelineEntry],
    conflicts: list[Conflict],
    critical_path: list[str],
    ahp: AhpResult,
) -> dict[str, object]:
    tasks = len(entries)
    with_source = sum(1 for entry in entries if entry.sources)
    dated = sum(1 for entry in entries if entry.deadline is not None)
    return {
        "tasks": tasks,
        "entries_with_source_quote": with_source,
        "source_grounding_rate": round(with_source / tasks, 4) if tasks else 0.0,
        "tasks_with_stated_deadline": dated,
        "critical_path_length": len(critical_path),
        "blocking_conflicts": sum(1 for conflict in conflicts if conflict.blocking),
        "review_items": sum(1 for conflict in conflicts if not conflict.blocking),
        "ahp_consistency_ratio": round(ahp.consistency_ratio, 4),
        "ahp_accepted": ahp.accepted,
        "as_of": request.as_of.isoformat(),
    }


# --------------------------------------------------------------------------- #
# Markdown rendering (report appendix / CLI)
# --------------------------------------------------------------------------- #
def render_markdown(plan: Plan, *, title: str | None = None) -> str:
    """Render a plan as Markdown tables that can be pasted into the report."""

    lines: list[str] = []
    heading = title or f"ExchangePrep timeline - {plan.case_label or plan.case_id}"
    lines.append(f"# {heading}")
    lines.append("")
    lines.append(f"- case: `{plan.case_id}` / programme `{plan.programme_id or 'n/a'}`")
    lines.append(f"- planning date (as_of): {plan.as_of.isoformat()}")
    lines.append(f"- feasibility: **{plan.feasibility}**")
    lines.append(
        f"- tasks: {plan.stats.get('tasks')}, "
        f"source-grounded: {plan.stats.get('entries_with_source_quote')} "
        f"({plan.stats.get('source_grounding_rate')}), "
        f"blocking conflicts: {plan.stats.get('blocking_conflicts')}, "
        f"review items: {plan.stats.get('review_items')}"
    )
    if plan.ahp:
        lines.append(
            f"- AHP weights: {plan.ahp.get('weights')} "
            f"(CR={plan.ahp.get('consistency_ratio')}, accepted={plan.ahp.get('accepted')})"
        )
    lines.append("")

    lines.append("## Anchors")
    lines.append("")
    lines.append("| anchor | date | state | label |")
    lines.append("| --- | --- | --- | --- |")
    for anchor in plan.anchors:
        lines.append(
            f"| `{anchor.kind}` | {anchor.anchor_date.isoformat() if anchor.anchor_date else 'unknown'} "
            f"| {anchor.state} | {anchor.label} |"
        )
    lines.append("")

    lines.append("## Ordered plan")
    lines.append("")
    lines.append(
        "| # | what to do | category | recommended start | earliest start | latest start | deadline "
        "| slack | critical | driver chain | state | priority |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for entry in plan.entries:
        lines.append(
            "| {order} | {title} (`{task_id}`) | {category} | {recommended} | {earliest} "
            "| {latest} | {deadline} | {slack} | {critical} | {driver} | {state} | {priority} |".format(
                order=entry.order,
                title=entry.title_plain or entry.title,
                task_id=entry.task_id,
                category=entry.category,
                recommended=entry.recommended_start.isoformat()
                if entry.recommended_start
                else "-",
                earliest=entry.earliest_start.isoformat() if entry.earliest_start else "-",
                latest=entry.latest_start.isoformat() if entry.latest_start else "-",
                deadline=entry.deadline.isoformat() if entry.deadline else "-",
                slack="-" if entry.float_days is None else entry.float_days,
                critical="yes" if entry.on_critical_path else "",
                driver="yes" if entry.on_driver_chain else "",
                state=entry.state,
                priority=f"{entry.priority.score:.3f}" if entry.priority else "-",
            )
        )
    lines.append("")

    if plan.entries:
        lines.append("## Why each date was chosen")
        lines.append("")
        for entry in plan.entries:
            lines.append(
                f"- `{entry.task_id}`: {entry.deadline_source_plain or entry.deadline_source}"
            )
        lines.append("")

    if plan.edges:
        lines.append("## Dependency edges")
        lines.append("")
        lines.append("| predecessor | successor | kind | reason |")
        lines.append("| --- | --- | --- | --- |")
        for edge in plan.edges:
            lines.append(
                f"| `{edge.predecessor}` | `{edge.successor}` | {edge.kind} | {edge.reason} |"
            )
        lines.append("")

    if plan.critical_path:
        lines.append("## Critical path")
        lines.append("")
        lines.append(f"- basis: `{plan.critical_path_basis}`")
        lines.append("- chain: " + " -> ".join(f"`{task}`" for task in plan.critical_path))
        lines.append("")

    if plan.conflicts:
        lines.append("## Conflicts and review items")
        lines.append("")
        lines.append("| blocking | kind | task | what it means | detail |")
        lines.append("| --- | --- | --- | --- | --- |")
        for conflict in plan.conflicts:
            lines.append(
                f"| {'yes' if conflict.blocking else 'no'} | {conflict.kind} "
                f"| `{conflict.task_id or '-'}` | {conflict.detail_plain or ''} | {conflict.detail} |"
            )
        lines.append("")

    lines.append("## Sources per task")
    lines.append("")
    for entry in plan.entries:
        if not entry.sources:
            lines.append(f"- `{entry.task_id}`: no source quote attached - verify manually")
            continue
        quotes = "; ".join(
            f"[{link.source_id}] \"{link.quote}\"" for link in entry.sources if link.quote
        )
        lines.append(
            f"- `{entry.task_id}`: {quotes or ', '.join(link.source_id for link in entry.sources)}"
        )
    lines.append("")

    if plan.notes:
        lines.append("## Planner notes")
        lines.append("")
        for note in plan.notes:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)
