"""Dependency modelling and timeline generation for ExchangePrep.

This package turns the requirement tasks produced by the web-collection and
extraction module (``models.Step``) into a *checked, dated, ordered* preparation
plan:

1. ``schema``     - the plan-time contract: tasks, anchors, priorities, timeline.
2. ``graph``       - the temporal dependency graph (DAG validation, topological
                     order, cycle reporting, critical path).
3. ``schedule``    - reverse scheduling (latest start / float), feasibility and
                     validity-window conflicts.
4. ``ahp``         - Analytic Hierarchy Process weights used for prioritisation.
5. ``timeline``    - the ordered, dated, source-linked plan and its explanations.

Design rule inherited from the project: **never invent a date**. A task whose
date or duration cannot be grounded stays on the timeline with state ``unknown``
and a stated reason.
"""

from planner.schema import (
    PLANNER_SCHEMA_VERSION,
    AhpComparison,
    Conflict,
    ConflictKind,
    DependencyEdge,
    DurationEstimate,
    EntryState,
    EvidenceLink,
    Plan,
    PlanRequest,
    PlanningAnchor,
    PlanningTask,
    PriorityBreakdown,
    StudentProfile,
    TaskOrigin,
    TaskSpec,
    TaskState,
    TimelineEntry,
    TimelineWeek,
    ValidityWindow,
    load_plan_request,
)
from planner.graph import DependencyGraph, build_graph
from planner.ahp import AhpResult, ahp_weights, consistency_ratio, priority_scores
from planner.schedule import ScheduleResult, schedule
from planner.timeline import build_plan, render_markdown

__all__ = [
    "PLANNER_SCHEMA_VERSION",
    "AhpComparison",
    "AhpResult",
    "Conflict",
    "ConflictKind",
    "DependencyEdge",
    "DependencyGraph",
    "DurationEstimate",
    "EvidenceLink",
    "Plan",
    "PlanRequest",
    "PlanningAnchor",
    "PlanningTask",
    "PriorityBreakdown",
    "ScheduleResult",
    "StudentProfile",
    "TaskOrigin",
    "TaskSpec",
    "TaskState",
    "TimelineEntry",
    "TimelineWeek",
    "ValidityWindow",
    "ahp_weights",
    "build_graph",
    "build_plan",
    "consistency_ratio",
    "load_plan_request",
    "priority_scores",
    "render_markdown",
    "schedule",
]
