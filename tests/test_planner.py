"""Tests for the dependency-modelling and timeline-generation module.

The suite is deliberately split along the module boundaries so a failure points
at one responsibility:

* ``DependencyGraphTests``   - graph construction, cycles, topological order,
                               critical path.
* ``SchedulingTests``        - anchors, reverse scheduling, slack, conflicts.
* ``ValidityWindowTests``    - the "score expires before departure" worked example.
* ``AhpTests``               - weights, consistency ratio, priority arithmetic.
* ``TimelineTests``          - order legality, dates inside their windows, stats.
* ``CuratedCaseTests``       - the three shipped manifests used as golden cases.
* ``ApiTests``               - the HTTP entry points used by the integration module.

No test writes outside the repository, so the suite runs inside a read-only or
workspace-write sandbox.
"""

import json
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from planner.ahp import (
    DEFAULT_CRITERIA,
    ahp_weights,
    consistency_ratio,
    pairwise_matrix,
    priority_scores,
    task_criteria,
)
from planner.graph import build_graph, find_cycles, topological_order
from planner.schedule import schedule, validity_expiry
from planner.schema import (
    AhpComparison,
    ConflictKind,
    Conflict,
    DurationEstimate,
    EvidenceLink,
    PlanningAnchor,
    PlanningTask,
    PlanRequest,
    StudentProfile,
    TaskSpec,
    ValidityWindow,
    load_plan_request,
)
from planner.timeline import build_plan, priority_order, render_markdown

CASE_DIR = Path(__file__).resolve().parent.parent / "planner" / "cases"
MAIN_CASE = CASE_DIR / "hkuww_main_round_2027.json"
VALID_CASE = CASE_DIR / "hkuww_main_round_2027_feasible.json"
CYCLE_CASE = CASE_DIR / "cycle_example.json"


def anchor(kind: str, day: str) -> PlanningAnchor:
    label = "Departure for the host country" if kind == "departure" else kind
    return PlanningAnchor(kind=kind, label=label, anchor_date=date.fromisoformat(day))


def task(
    task_id: str,
    *,
    prerequisites: list[str] | None = None,
    deadline: str | None = None,
    deadline_basis: str | None = None,
    relative: dict | None = None,
    duration: int = 1,
    buffer_days: int = 0,
    validity: list[ValidityWindow] | None = None,
    basis: str = "stated",
    **extra,
) -> PlanningTask:
    """Build a planning task; with neither deadline nor relative, none is stated."""

    if deadline_basis is None:
        if deadline is not None:
            deadline_basis = "fixed"
        elif relative is not None:
            deadline_basis = "relative"
        else:
            deadline_basis = "none"
    return PlanningTask(
        id=task_id,
        title=task_id.replace("_", " "),
        prerequisites=prerequisites or [],
        deadline_basis=deadline_basis,
        deadline=date.fromisoformat(deadline) if deadline else None,
        deadline_relative=relative,
        deadline_state=extra.pop("deadline_state", "stated"),
        duration=DurationEstimate(min_days=duration, max_days=duration, basis=basis),
        buffer_days=buffer_days,
        validity=validity or [],
        **extra,
    )


def request_with(tasks, *, as_of="2026-10-01", anchors=None, **extra) -> PlanRequest:
    return PlanRequest(
        case_id="unit",
        as_of=date.fromisoformat(as_of),
        anchors=anchors
        or [
            anchor("today", as_of),
            anchor("departure", "2027-08-22"),
        ],
        tasks=tasks,
        student=StudentProfile(label="unit test profile"),
        **extra,
    )


class DependencyGraphTests(unittest.TestCase):
    def test_topological_order_honours_prerequisites(self):
        tasks = [
            task("c", prerequisites=["b"]),
            task("b", prerequisites=["a"]),
            task("a"),
        ]
        graph = build_graph(tasks)
        self.assertTrue(graph.is_acyclic)
        self.assertLess(
            graph.topological_order.index("a"), graph.topological_order.index("b")
        )
        self.assertLess(
            graph.topological_order.index("b"), graph.topological_order.index("c")
        )

    def test_order_is_deterministic_for_independent_tasks(self):
        tasks = [task("z"), task("a"), task("m")]
        first = build_graph(tasks).topological_order
        second = build_graph(list(reversed(tasks))).topological_order
        self.assertEqual(first, second)
        self.assertEqual(first, ["a", "m", "z"])

    def test_cycle_is_detected_and_reported(self):
        tasks = [
            task("shortlist", prerequisites=["letter"]),
            task("letter", prerequisites=["shortlist"]),
            task("submit", prerequisites=["letter"]),
        ]
        graph = build_graph(tasks)
        self.assertFalse(graph.is_acyclic)
        self.assertEqual(len(graph.cycles), 1)
        self.assertEqual(set(graph.cycles[0]), {"shortlist", "letter"})
        # Only the unresolved (cyclic) part is withheld; nothing is invented.
        self.assertEqual(graph.topological_order, [])
        self.assertEqual(set(graph.tasks), {"shortlist", "letter", "submit"})

    def test_self_loop_is_ignored_not_fatal(self):
        graph = build_graph([task("a", prerequisites=["a"])])
        self.assertTrue(graph.is_acyclic)
        self.assertEqual(graph.edges, [])

    def test_missing_predecessor_is_recorded_as_dangling(self):
        graph = build_graph([task("a", prerequisites=["ghost"])])
        self.assertEqual(graph.dangling_predecessors, {"a": ["ghost"]})
        self.assertTrue(graph.is_acyclic)

    def test_valid_until_edges_do_not_change_topological_order(self):
        older = task("test")
        newer = task("nomination", valid_until=["test"])
        graph = build_graph([older, newer])
        kinds = {(edge.predecessor, edge.successor): edge.kind for edge in graph.edges}
        self.assertEqual(kinds[("test", "nomination")], "valid_until")
        self.assertEqual(graph.prerequisite_predecessors("nomination"), [])

    def test_critical_path_uses_zero_slack_chain(self):
        # "a" -> "b" must both be finished before the departure anchor.  "a" is
        # the determinant (its own deadline is exactly its earliest finish), and
        # "side" keeps a month of slack of its own.
        tasks = [
            task("a", duration=30, deadline_basis="relative",
                 relative={"anchor": "departure", "offset_days": -91}),
            task("b", prerequisites=["a"], duration=10,
                 deadline_basis="relative", relative={"anchor": "departure", "offset_days": -1}),
            task("side", duration=1, deadline_basis="relative",
                 relative={"anchor": "departure", "offset_days": -60}),
        ]
        request = request_with(
            tasks,
            as_of="2026-10-01",
            anchors=[anchor("today", "2026-10-01"), anchor("departure", "2027-01-30")],
        )
        result = schedule(request)
        self.assertEqual(result.float_days["a"], 0)
        self.assertEqual(result.float_days["side"], 60)
        path = result.graph.critical_path(result.float_days, result.durations)
        self.assertEqual(path, ["a"])
        self.assertNotIn("side", path)

    def test_longest_chain_is_used_when_nothing_has_zero_slack(self):
        tasks = [
            task("a", duration=30, deadline_basis="relative",
                 relative={"anchor": "departure", "offset_days": -90}),
            task("b", prerequisites=["a"], duration=10,
                 deadline_basis="relative", relative={"anchor": "departure", "offset_days": -60}),
        ]
        result = schedule(request_with(tasks))
        self.assertNotIn(0, list(result.float_days.values()))
        self.assertEqual(result.graph.longest_chain(result.durations), ["a", "b"])

    def test_longest_chain_is_empty_for_cyclic_graph(self):
        graph = build_graph(
            [task("a", prerequisites=["b"]), task("b", prerequisites=["a"])]
        )
        self.assertEqual(graph.longest_chain({"a": 1, "b": 1}), [])

    def test_find_cycles_returns_each_cycle_once(self):
        tasks = [
            task("a", prerequisites=["b"]),
            task("b", prerequisites=["c"]),
            task("c", prerequisites=["a"]),
        ]
        graph = build_graph(tasks)
        self.assertEqual(len(graph.cycles), 1)
        self.assertEqual(set(graph.cycles[0]), {"a", "b", "c"})


class SchedulingTests(unittest.TestCase):
    def test_relative_deadline_is_resolved_from_its_anchor(self):
        tasks = [
            task("prepare", deadline_basis="relative",
                 relative={"anchor": "departure", "offset_days": -30}, duration=5)
        ]
        result = schedule(request_with(tasks))
        self.assertEqual(result.deadline["prepare"], date(2027, 7, 23))
        self.assertIn("Departure", result.deadline_source["prepare"])

    def test_undated_anchor_refuses_to_invent_a_date(self):
        tasks = [
            task("prepare", deadline_basis="relative",
                 relative={"anchor": "programme_start", "offset_days": -10})
        ]
        anchors = [
            anchor("today", "2026-10-01"),
            anchor("departure", "2027-08-22"),
            PlanningAnchor(kind="programme_start", label="host start", anchor_date=None),
        ]
        result = schedule(request_with(tasks, anchors=anchors))
        self.assertIsNone(result.deadline["prepare"])
        kinds = {conflict.kind for conflict in result.conflicts}
        self.assertIn("unknown_anchor", kinds)
        self.assertTrue(all(c.blocking for c in result.conflicts if c.kind == "unknown_anchor"))

    def test_latest_start_uses_duration_and_buffer(self):
        tasks = [task("prepare", deadline="2026-12-11", duration=5, buffer_days=3)]
        result = schedule(request_with(tasks))
        self.assertEqual(result.latest_start["prepare"], date(2026, 12, 3))
        self.assertEqual(result.float_days["prepare"], (date(2026, 12, 3) - date(2026, 10, 1)).days)

    def test_latest_start_is_capped_by_successor(self):
        tasks = [
            task("prepare", deadline_basis="none", duration=5),
            task("submit", prerequisites=["prepare"], deadline="2026-11-01"),
        ]
        result = schedule(request_with(tasks))
        self.assertIsNone(result.deadline["prepare"])
        # The successor's latest start is 2026-10-31 (one-day task plus a two-day
        # buffer before its 2026-11-01 deadline), which is what caps the
        # predecessor's latest finish.
        self.assertEqual(result.latest_finish["prepare"], date(2026, 10, 31))
        self.assertEqual(result.latest_start["prepare"], date(2026, 10, 26))
        self.assertIn("successor", result.deadline_source["prepare"])

    def test_tasks_without_any_deadline_fall_back_to_the_horizon(self):
        result = schedule(request_with([task("prepare", deadline_basis="none")]))
        self.assertEqual(result.latest_finish["prepare"], date(2027, 8, 22))
        self.assertIn("horizon", result.deadline_source["prepare"])

    def test_deadline_in_the_past_is_reported_as_infeasible(self):
        tasks = [task("submit", deadline="2026-09-01", duration=1)]
        result = schedule(request_with(tasks))
        kinds = [conflict.kind for conflict in result.conflicts]
        self.assertIn("earliest_after_deadline", kinds)
        self.assertEqual(result.task_state["submit"], "infeasible")
        self.assertEqual(result.feasibility, "infeasible")

    def test_prerequisites_alone_can_exhaust_the_window(self):
        tasks = [
            task("prepare", deadline_basis="none", duration=30),
            task("submit", prerequisites=["prepare"], deadline="2026-10-10", duration=1),
        ]
        result = schedule(request_with(tasks))
        kinds = [conflict.kind for conflict in result.conflicts]
        self.assertIn("earliest_after_deadline", kinds)
        self.assertIn("dependency_impossible", kinds)

    def test_unknown_duration_is_a_review_item_not_a_failure(self):
        tasks = [task("prepare", deadline="2027-01-10", duration=0, basis="unknown")]
        result = schedule(request_with(tasks))
        soft = [conflict for conflict in result.conflicts if conflict.kind == "missing_duration"]
        self.assertEqual(len(soft), 1)
        self.assertFalse(soft[0].blocking)
        self.assertEqual(result.feasibility, "at_risk")

    def test_relative_deadline_without_anchor_is_reported(self):
        tasks = [
            task(
                "submit",
                deadline_basis="relative",
                relative=None,
                deadline_state="stated",
            )
        ]
        tasks[0].deadline_raw = "after receiving the host offer"
        result = schedule(request_with(tasks))
        unresolved = [
            conflict
            for conflict in result.conflicts
            if conflict.kind == "relative_deadline_unresolved"
        ]
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unresolved[0].blocking)
        self.assertIn("after receiving the host offer", unresolved[0].detail)
        self.assertIsNone(result.deadline["submit"])

    def test_unknown_predecessor_is_reported(self):
        result = schedule(request_with([task("submit", prerequisites=["ghost"])]))
        kinds = [conflict.kind for conflict in result.conflicts]
        self.assertIn("unknown_predecessor", kinds)

    def test_feasibility_is_feasible_when_everything_has_room(self):
        tasks = [
            task("prepare", deadline="2026-11-01", duration=3),
            task("submit", prerequisites=["prepare"], deadline="2026-12-11", duration=2),
        ]
        result = schedule(request_with(tasks))
        self.assertEqual(result.conflicts, [])
        self.assertEqual(result.feasibility, "feasible")


class ValidityWindowTests(unittest.TestCase):
    """The worked example from the project plan: an expiring IELTS score."""

    def _ielts(self, taken: str | None, months: int | None = 24, expiry: str | None = None):
        return ValidityWindow(
            label="IELTS validity",
            taken_date=date.fromisoformat(taken) if taken else None,
            valid_months=months,
            expiry_date=date.fromisoformat(expiry) if expiry else None,
            must_cover="departure",
            deadline_kind="retention",
        )

    def test_expiry_is_computed_with_calendar_months(self):
        self.assertEqual(
            validity_expiry(self._ielts("2025-01-31", 1)), date(2025, 2, 28)
        )

    def test_expired_score_blocks_departure(self):
        tasks = [
            task(
                "ielts",
                deadline_basis="none",
                duration=20,
                validity=[self._ielts("2023-09-09")],
            )
        ]
        result = schedule(request_with(tasks))
        conflict = next(
            conflict
            for conflict in result.conflicts
            if conflict.kind == "validity_expires_before_departure"
        )
        self.assertTrue(conflict.blocking)
        self.assertIn("2025-09-09", conflict.detail)
        self.assertIn("2025-08-22", conflict.detail)  # latest acceptable issue date
        self.assertEqual(result.task_state["ielts"], "infeasible")

    def test_valid_score_is_reported_as_covering_the_anchor(self):
        # 24 months from 2026-09-10 reaches 2028-09-10, which covers the
        # 2027-08-22 departure, so there must be no validity conflict.
        tasks = [
            task("ielts", deadline_basis="none", duration=5, validity=[self._ielts("2026-09-10")])
        ]
        result = schedule(request_with(tasks))
        kinds = [conflict.kind for conflict in result.conflicts]
        self.assertNotIn("validity_expires_before_departure", kinds)
        self.assertTrue(any("covers Departure" in note for note in result.notes))

    def test_missing_validity_period_is_a_review_item(self):
        tasks = [
            task("ielts", deadline_basis="none", duration=5, validity=[self._ielts(None, None)])
        ]
        result = schedule(request_with(tasks))
        soft = [conflict for conflict in result.conflicts if conflict.kind == "validity_unknown"]
        self.assertEqual(len(soft), 1)
        self.assertFalse(soft[0].blocking)

    def test_schedule_kind_window_never_becomes_a_deadline(self):
        window = ValidityWindow(
            label="score must be recent",
            valid_months=24,
            must_cover="departure",
            deadline_kind="schedule",
        )
        tasks = [task("ielts", deadline_basis="none", duration=5, validity=[window])]
        result = schedule(request_with(tasks))
        self.assertIsNone(result.deadline["ielts"])
        kinds = [conflict.kind for conflict in result.conflicts]
        self.assertNotIn("validity_expires_before_departure", kinds)

    def test_validity_deadline_is_still_reported_for_the_report(self):
        tasks = [task("ielts", deadline_basis="none", duration=5, validity=[self._ielts("2023-09-09")])]
        result = schedule(request_with(tasks))
        self.assertEqual(result.validity_deadline["ielts"], date(2025, 9, 9))


class AhpTests(unittest.TestCase):
    def test_default_weights_favour_urgency_and_are_consistent(self):
        result = ahp_weights()
        self.assertEqual(result.criteria, list(DEFAULT_CRITERIA))
        self.assertAlmostEqual(sum(result.weights.values()), 1.0, places=6)
        self.assertGreater(result.weights["urgency"], result.weights["effort"])
        self.assertLess(result.consistency_ratio, 0.10)
        self.assertTrue(result.accepted)

    def test_reciprocal_matrix(self):
        matrix = pairwise_matrix(
            ["x", "y"], [AhpComparison(a="x", b="y", value=3.0)]
        )
        self.assertEqual(matrix[0][0], 1.0)
        self.assertEqual(matrix[1][0], 1.0 / 3.0)

    def test_inconsistent_judgements_are_flagged(self):
        comparisons = [
            AhpComparison(a="urgency", b="impact", value=9.0),
            AhpComparison(a="urgency", b="flexibility", value=9.0),
            AhpComparison(a="urgency", b="effort", value=9.0),
            AhpComparison(a="impact", b="flexibility", value=1.0 / 9.0),
            AhpComparison(a="impact", b="effort", value=1.0 / 9.0),
            AhpComparison(a="flexibility", b="effort", value=9.0),
        ]
        result = ahp_weights(comparisons=comparisons)
        self.assertGreater(result.consistency_ratio, 0.10)
        self.assertFalse(result.accepted)

    def test_unknown_comparison_criterion_is_rejected(self):
        with self.assertRaises(ValueError):
            ahp_weights(comparisons=[AhpComparison(a="speed", b="impact", value=2.0)])

    def test_consistency_ratio_is_zero_for_two_criteria(self):
        index, ratio = consistency_ratio(["a", "b"], 2.5)
        self.assertEqual((index, ratio), (0.0, 0.0))

    def test_urgency_criterion_decays_with_time(self):
        soon = task_criteria(
            task("a"), duration_days=1, float_days=1, days_to_deadline=3
        )
        later = task_criteria(
            task("a"), duration_days=1, float_days=1, days_to_deadline=90
        )
        self.assertGreater(soon["urgency"], later["urgency"])

    def test_priority_scores_are_deterministic_and_bounded(self):
        tasks = [task("a", duration=3), task("b", duration=30, optional=True)]
        ahp = ahp_weights()
        first = priority_scores(
            tasks,
            durations={"a": 3, "b": 30},
            float_days={"a": 1, "b": 60},
            days_to_deadline={"a": 10, "b": 120},
            ahp=ahp,
        )
        second = priority_scores(
            tasks,
            durations={"a": 3, "b": 30},
            float_days={"a": 1, "b": 60},
            days_to_deadline={"a": 10, "b": 120},
            ahp=ahp,
        )
        self.assertEqual(first, second)
        self.assertGreater(first["a"][0], first["b"][0])


class TimelineTests(unittest.TestCase):
    def _plan(self):
        return build_plan(load_plan_request(MAIN_CASE))

    def test_work_order_is_a_legal_topological_order(self):
        plan = self._plan()
        order = [entry.task_id for entry in plan.entries]
        self.assertEqual(len(plan.entries), plan.stats["tasks"])
        for edge in plan.edges:
            if edge.kind != "prerequisite":
                continue
            self.assertLess(order.index(edge.predecessor), order.index(edge.successor))

    def test_priority_never_overrides_a_prerequisite(self):
        tasks = [
            task("slow_low_priority", duration=1),
            task("urgent_successor", prerequisites=["slow_low_priority"], duration=1),
        ]
        graph = build_graph(tasks)
        order = priority_order(graph, {"slow_low_priority": 0.1, "urgent_successor": 1.0})
        self.assertEqual(order, ["slow_low_priority", "urgent_successor"])

    def test_dates_stay_inside_the_legal_window(self):
        for entry in self._plan().entries:
            if entry.earliest_start and entry.latest_start:
                self.assertLessEqual(entry.earliest_start, entry.latest_start)
            if entry.recommended_start and entry.latest_start:
                self.assertLessEqual(entry.recommended_start, entry.latest_start)

    def test_sources_and_documents_travel_with_the_entry(self):
        entry = next(
            item for item in self._plan().entries if item.task_id == "hkuww_submission"
        )
        self.assertTrue(entry.sources)
        self.assertIn("official transcript", entry.documents)

    def test_source_grounding_rate_is_measured(self):
        plan = self._plan()
        self.assertEqual(plan.stats["source_grounding_rate"], 1.0)
        self.assertEqual(plan.stats["entries_with_source_quote"], plan.stats["tasks"])

    def test_conflicts_are_ranked_blocking_first(self):
        plan = self._plan()
        blocking = [conflict.blocking for conflict in plan.conflicts]
        self.assertEqual(blocking, sorted(blocking, reverse=True))
        self.assertTrue(any(c.kind == "validity_expires_before_departure" for c in plan.conflicts))

    def test_markdown_contains_the_plan_tables(self):
        markdown = render_markdown(self._plan())
        for heading in (
            "## Anchors",
            "## Ordered plan",
            "## Dependency edges",
            "## Conflicts and review items",
            "## Sources per task",
        ):
            self.assertIn(heading, markdown)
        self.assertIn("hkuww_submission", markdown)

    def test_critical_flag_never_over_claims(self):
        plan = self._plan()
        for entry in plan.entries:
            self.assertEqual(entry.on_critical_path, entry.float_days == 0)
        # The main case has no zero-slack task, so the reported chain is the
        # longest dependency chain and is labelled as such.
        self.assertEqual(plan.critical_path_basis, "longest_dependency_chain")
        self.assertTrue(plan.critical_path)
        self.assertTrue(all(entry.float_days != 0 for entry in plan.entries))

    def test_weeks_cover_every_recommended_start(self):
        plan = self._plan()
        for entry in plan.entries:
            if entry.recommended_start is None:
                continue
            self.assertTrue(
                any(
                    week.week_start <= entry.recommended_start <= week.week_end
                    for week in plan.weeks
                )
            )

    def test_weekly_capacity_overflow_becomes_a_warning(self):
        payload = json.loads(MAIN_CASE.read_text(encoding="utf-8"))
        payload["weekly_capacity_days"] = 0.5
        plan = build_plan(PlanRequest.model_validate(payload))
        warnings = [warning for entry in plan.entries for warning in entry.warnings]
        self.assertTrue(any("capacity" in warning for warning in warnings))

    def test_plan_is_json_serialisable(self):
        payload = json.loads(build_plan(load_plan_request(MAIN_CASE)).model_dump_json())
        self.assertEqual(payload["plan_schema_version"], "1.0")
        self.assertIn("entries", payload)


class CuratedCaseTests(unittest.TestCase):
    """The shipped manifests act as golden cases for the report."""

    def test_main_case_flags_the_expired_language_score(self):
        plan = build_plan(load_plan_request(MAIN_CASE))
        self.assertEqual(plan.feasibility, "infeasible")
        blocking = [conflict for conflict in plan.conflicts if conflict.blocking]
        self.assertEqual(len(blocking), 1)
        self.assertEqual(blocking[0].kind, "validity_expires_before_departure")
        self.assertEqual(blocking[0].task_id, "ielts_retake")
        self.assertEqual(len(plan.entries), 14)

    def test_valid_score_case_is_schedulable(self):
        plan = build_plan(load_plan_request(VALID_CASE))
        self.assertNotEqual(plan.feasibility, "infeasible")
        self.assertEqual(plan.stats["blocking_conflicts"], 0)
        self.assertTrue(
            all(entry.recommended_start for entry in plan.entries if entry.latest_start)
        )

    def test_cycle_case_is_refused_and_explained(self):
        plan = build_plan(load_plan_request(CYCLE_CASE))
        self.assertEqual(plan.feasibility, "infeasible")
        cycles = [conflict for conflict in plan.conflicts if conflict.kind == "dependency_cycle"]
        self.assertEqual(len(cycles), 1)
        self.assertIn("reference_letter", cycles[0].detail)
        self.assertIn("shortlist", cycles[0].detail)
        self.assertTrue(plan.unscheduled)

    def test_every_shipped_case_has_an_anchor_and_loads(self):
        files = sorted(CASE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(files), 3)
        for path in files:
            request = load_plan_request(path)
            self.assertTrue(any(anchor.kind == "departure" for anchor in request.anchors))
            self.assertTrue(request.tasks)

    def test_case_files_use_departure_anchor_consistently(self):
        plan = build_plan(load_plan_request(MAIN_CASE))
        self.assertIn("planning horizon: 2027-08-22", plan.notes)


class ResearchAdapterTests(unittest.TestCase):
    """The bridge from the extraction module's ResearchResult into plan tasks."""

    def test_adapter_maps_steps_and_keeps_evidence(self):
        from models import (
            Deadline,
            Evidence,
            Fact,
            Programme,
            ResearchRequest,
            ResearchResult,
            Source,
            Step,
        )
        from planner.schema import adapt_research_result

        step = Step(
            id="host_application",
            category="host_application",
            title="Apply to the host",
            action=Fact(
                value="Submit the host application",
                state="verified",
                evidence=[Evidence(source_id="S1", quote="Apply directly to the host.")],
            ),
            deadline=Deadline(kind="fixed", date="2027-03-01", state="verified"),
        )
        result = ResearchResult(
            request=ResearchRequest(
                destination="United Kingdom",
                start_date=date(2027, 6, 1),
                end_date=date(2027, 8, 31),
                programme_type="exchange",
            ),
            generated_at=date(2026, 10, 2),
            provider="unit test",
            programmes=[
                Programme(
                    id="p1",
                    name="Partner exchange",
                    programme_type="exchange",
                    destination="United Kingdom",
                    steps=[step],
                )
            ],
            sources=[
                Source(
                    id="S1",
                    url="https://example.edu/apply",
                    title="Apply",
                    publisher_type="host",
                    retrieved_at=date(2026, 10, 2),
                    read_status="read",
                )
            ],
        )
        request = adapt_research_result(
            result,
            task_specs={
                "host_application": TaskSpec(
                    task_id="host_application",
                    duration=DurationEstimate(min_days=3, max_days=5, basis="inferred_convention"),
                )
            },
            anchors=[anchor("departure", "2027-06-20")],
        )
        self.assertEqual(request.as_of, date(2026, 10, 2))
        self.assertEqual(len(request.tasks), 1)
        mapped = request.tasks[0]
        self.assertEqual(mapped.deadline, date(2027, 3, 1))
        self.assertEqual(mapped.origin, "extracted")
        self.assertEqual(mapped.duration.max_days, 5)
        self.assertEqual(mapped.evidence[0].source_id, "S1")

    def test_adapter_requires_a_departure_anchor(self):
        from models import Programme, ResearchRequest, ResearchResult
        from planner.schema import adapt_research_result

        result = ResearchResult(
            request=ResearchRequest(
                destination="United Kingdom",
                start_date=date(2027, 6, 1),
                end_date=date(2027, 8, 31),
                programme_type="exchange",
            ),
            generated_at=date(2026, 10, 2),
            provider="unit test",
            programmes=[
                Programme(
                    id="p1",
                    name="Partner exchange",
                    programme_type="exchange",
                    destination="United Kingdom",
                )
            ],
        )
        with self.assertRaises(ValueError):
            adapt_research_result(result, anchors=[anchor("today", "2026-10-02")])

    def test_offline_sample_snapshot_is_plannable(self):
        """End-to-end bridge: the shipped sample.json plus anchors must plan."""

        from models import ResearchResult
        from planner.schema import adapt_research_result

        root = Path(__file__).resolve().parent.parent
        result = ResearchResult.model_validate_json(
            (root / "sample.json").read_text(encoding="utf-8")
        )
        anchors = [
            anchor("today", "2026-09-28"),
            anchor("departure", "2027-06-20"),
        ]
        request = adapt_research_result(result, anchors=anchors, as_of=date(2026, 10, 4))
        plan = build_plan(request)
        self.assertEqual(plan.stats["tasks"], 6)
        # Five of the six steps carry a quote; `visa_check` has none in the
        # offline sample, and the ungrounded step must be visible rather than
        # silently treated as sourced.
        self.assertEqual(plan.stats["entries_with_source_quote"], 5)
        self.assertLess(plan.stats["source_grounding_rate"], 1.0)
        visa = next(entry for entry in plan.entries if entry.task_id == "visa_check")
        self.assertEqual(visa.sources, [])
        # The sample's relative "after receiving the offer" deadline has no
        # anchor, so it must be reported instead of resolved to a date.
        kinds = {conflict.kind for conflict in plan.conflicts}
        self.assertIn("relative_deadline_unresolved", kinds)
        unresolved = next(
            conflict
            for conflict in plan.conflicts
            if conflict.kind == "relative_deadline_unresolved"
        )
        self.assertEqual(unresolved.task_id, "hku_registration_submit")
        self.assertIsNone(
            next(entry for entry in plan.entries if entry.task_id == "hku_registration_submit").deadline
        )


class NetworkXCrossCheckTests(unittest.TestCase):
    """Independent cross-check of our graph algorithms against NetworkX.

    NetworkX (the graph library named in the project plan) is an *optional*
    dependency: install ``requirements-planner-optional.txt`` to run these tests.
    They exist to show the hand-written algorithms agree with a standard library,
    not because the planner needs it.
    """

    def setUp(self):
        try:
            import networkx  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the environment
            self.skipTest("networkx is not installed (optional cross-check)")

    def _random_tasks(self, seed: int):
        import random

        rng = random.Random(seed)
        tasks = []
        for index in range(8):
            # Only earlier ids may be prerequisites, so the graph is acyclic by
            # construction; the graph tests cover the cyclic cases instead.
            candidates = [f"t{other}" for other in range(index)]
            chosen = rng.sample(candidates, k=min(len(candidates), rng.randint(0, 2)))
            tasks.append(task(f"t{index}", prerequisites=chosen, duration=rng.randint(1, 20)))
        return tasks

    def test_topological_order_matches_networkx_legality(self):
        import networkx as nx

        for seed in range(10):
            tasks = self._random_tasks(seed)
            graph = build_graph(tasks)
            reference = nx.DiGraph()
            reference.add_nodes_from(graph.tasks)
            reference.add_edges_from(
                (edge.predecessor, edge.successor) for edge in graph.edges
            )
            position = {task_id: index for index, task_id in enumerate(graph.topological_order)}
            self.assertTrue(nx.is_directed_acyclic_graph(reference))
            for edge in graph.edges:
                self.assertLess(position[edge.predecessor], position[edge.successor])

    def test_longest_chain_length_matches_networkx_dag_path(self):
        import networkx as nx

        for seed in range(10):
            tasks = self._random_tasks(seed)
            graph = build_graph(tasks)
            durations = {item.id: item.duration.max_days for item in tasks}
            reference = nx.DiGraph()
            reference.add_nodes_from(durations)
            for edge in graph.edges:
                reference.add_edge(
                    edge.predecessor, edge.successor, weight=durations[edge.successor]
                )
            ours = sum(durations[item] for item in graph.longest_chain(durations))
            theirs = nx.dag_longest_path_length(reference, weight="weight")
            self.assertEqual(ours, theirs)


class PlainLanguageTests(unittest.TestCase):
    """The module must be readable by someone who is not a programmer."""

    def setUp(self):
        self.plan = build_plan(load_plan_request(MAIN_CASE))

    def test_deadline_source_is_rewritten_in_plain_chinese(self):
        from planner.explain import plain_deadline_source

        self.assertEqual(
            plain_deadline_source("fixed deadline from source"), "官网直接写明的截止日期"
        )
        self.assertEqual(
            plain_deadline_source("-30 days from HKUWW Main Round application deadline (2026-12-11)"),
            "从「HKUWW Main Round application deadline」(2026-12-11) 往前推 30 天",
        )
        self.assertEqual(
            plain_deadline_source("deadline unknown"), "官网没有写，需要你去确认"
        )

    def test_plain_deadline_source_keeps_chained_constraints(self):
        from planner.explain import plain_deadline_source

        text = plain_deadline_source(
            "-120 days from Host start (2027-09-20); limited by latest start of successor host_offer"
        )
        self.assertIn("往前推 120 天", text)
        self.assertIn("同时受限于", text)

    def test_every_entry_has_plain_wording(self):
        for entry in self.plan.entries:
            self.assertTrue(entry.title_plain, entry.task_id)
            self.assertTrue(entry.action_plain, entry.task_id)
            self.assertTrue(entry.state_plain, entry.task_id)
            self.assertTrue(entry.deadline_source_plain, entry.task_id)
            # No leftover internal bookkeeping in the reader-facing sentence.
            self.assertNotIn("days from", entry.deadline_source_plain)
            self.assertNotIn("_", entry.state_plain)

    def test_blocking_conflict_explains_itself_and_gives_advice(self):
        conflict = self.plan.conflicts[0]
        self.assertTrue(conflict.blocking)
        self.assertIn("材料会在需要用到它之前就过期", conflict.detail_plain)
        self.assertIn("请重新考一次", conflict.detail_plain)
        # The precise wording stays available for the report.
        self.assertIn("expires", conflict.detail)

    def test_entry_carries_its_own_plain_problems(self):
        entry = next(e for e in self.plan.entries if e.task_id == "ielts_retake")
        self.assertEqual(len(entry.blocking_plain), 1)
        self.assertIn("过期", entry.blocking_plain[0])
        self.assertEqual(entry.blocking_plain[0], self.plan.conflicts[0].detail_plain)

    def test_notes_are_rewritten_but_the_originals_are_kept(self):
        self.assertTrue(self.plan.notes_plain)
        self.assertTrue(any("所有准备工作必须在" in note for note in self.plan.notes_plain))
        self.assertTrue(any(note.startswith("planning horizon:") for note in self.plan.notes))

    def test_every_conflict_kind_has_plain_wording_and_advice(self):
        from planner.explain import CONFLICT_ADVICE, CONFLICT_PLAIN

        for kind in ConflictKind.__args__:
            self.assertIn(kind, CONFLICT_PLAIN, kind)
            self.assertTrue(CONFLICT_ADVICE.get(kind), kind)

    def test_curated_cases_are_fully_translated(self):
        for path in sorted(CASE_DIR.glob("*.json")):
            plan = build_plan(load_plan_request(path))
            for entry in plan.entries:
                self.assertNotEqual(entry.title_plain, entry.task_id, path.name)
                # Titles must be Chinese, not the extracted English sentence.
                self.assertRegex(entry.title_plain or "", r"[\u4e00-\u9fff]", path.name)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_case_listing_is_readable(self):
        response = self.client.get("/api/planner/cases")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(len(payload["cases"]), 3)
        self.assertTrue(all(case["readable"] for case in payload["cases"]))

    def test_plan_endpoint_returns_the_timeline(self):
        response = self.client.get("/api/planner/plan/hkuww-main-round-s1-2027")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan_schema_version"], "1.0")
        self.assertEqual(payload["feasibility"], "infeasible")
        self.assertTrue(payload["entries"])

    def test_plan_endpoint_accepts_an_as_of_override(self):
        response = self.client.get(
            "/api/planner/plan/hkuww-main-round-s1-2027", params={"as_of": "2025-01-31"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["as_of"], "2025-01-31")

    def test_markdown_format_is_available_for_the_report(self):
        response = self.client.get(
            "/api/planner/plan/hkuww-main-round-s1-2027", params={"format": "markdown"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("## Ordered plan", response.json()["markdown"])

    def test_unknown_case_is_404(self):
        response = self.client.get("/api/planner/plan/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_post_accepts_a_manifest(self):
        payload = json.loads(VALID_CASE.read_text(encoding="utf-8"))
        response = self.client.post("/api/planner/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()["feasibility"], "infeasible")

    def test_post_rejects_a_manifest_without_departure_anchor(self):
        payload = json.loads(VALID_CASE.read_text(encoding="utf-8"))
        payload["anchors"] = [a for a in payload["anchors"] if a["kind"] != "departure"]
        response = self.client.post("/api/planner/plan", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_schema_endpoint_publishes_the_contract(self):
        response = self.client.get("/api/planner/schema")
        self.assertEqual(response.status_code, 200)
        self.assertIn("plan_request_schema", response.json())


if __name__ == "__main__":
    unittest.main()
