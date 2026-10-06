"""Command-line entry point for the dependency-modelling and timeline module.

Examples
--------
Plan a curated case and print the timeline::

    python -m planner.cli plan --case planner/cases/hkuww_main_round_2027.json

Write the plan JSON and the Markdown appendix::

    python -m planner.cli plan --case planner/cases/hkuww_main_round_2027.json \\
        --json out/timeline.json --markdown out/timeline.md

Plan directly from a research snapshot produced by the extraction module::

    python -m planner.cli plan --research sample.json --anchors anchors.json
"""

from __future__ import annotations

import argparse
import json

from models import ResearchResult
from planner.schema import (
    EvidenceLink,
    PlanningAnchor,
    StudentProfile,
    load_plan_request,
    parse_iso_date,
)
from planner.timeline import build_plan, render_markdown


def _load_anchors(path: str) -> list[PlanningAnchor]:
    payload = json.loads(open(path, encoding="utf-8").read())
    if isinstance(payload, dict):
        payload = payload.get("anchors", [])
    anchors = []
    for item in payload:
        anchors.append(
            PlanningAnchor(
                kind=item["kind"],
                label=item.get("label", item["kind"]),
                anchor_date=parse_iso_date(item.get("anchor_date") or item.get("date")),
                state=item.get("state", "stated"),
                evidence=[
                    EvidenceLink(**evidence) for evidence in item.get("evidence", [])
                ],
            )
        )
    return anchors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="planner", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="build an ordered, dated plan")
    source = plan_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", help="path to a curated planning manifest")
    source.add_argument("--research", help="path to a research result JSON (models.ResearchResult)")
    plan_parser.add_argument("--anchors", help="anchor JSON for --research mode")
    plan_parser.add_argument("--specs", help="task-spec JSON keyed by step id for --research mode")
    plan_parser.add_argument("--as-of", dest="as_of", help="planning date (YYYY-MM-DD)")
    plan_parser.add_argument("--json", dest="json_out", help="write the plan JSON here")
    plan_parser.add_argument("--markdown", dest="markdown_out", help="write the Markdown appendix here")
    plan_parser.add_argument("--quiet", action="store_true", help="do not print the Markdown plan")

    args = parser.parse_args(argv)

    if args.case:
        request = load_plan_request(args.case)
    else:
        result = ResearchResult.model_validate_json(open(args.research, encoding="utf-8").read())
        if not args.anchors:
            parser.error("--research requires --anchors so dates are never guessed")
        from planner.schema import TaskSpec, adapt_research_result

        specs = {}
        if args.specs:
            payload = json.loads(open(args.specs, encoding="utf-8").read())
            specs = {
                step_id: TaskSpec.model_validate({"task_id": step_id, **payload[step_id]})
                for step_id in payload
            }
        request = adapt_research_result(
            result,
            task_specs=specs,
            anchors=_load_anchors(args.anchors),
            profile=StudentProfile(label="from research snapshot"),
            as_of=parse_iso_date(args.as_of) if args.as_of else None,
        )
        if args.as_of:
            request = request.model_copy(update={"as_of": parse_iso_date(args.as_of)})

    plan = build_plan(request)
    markdown = render_markdown(plan)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            handle.write(plan.model_dump_json(indent=2))
    if args.markdown_out:
        with open(args.markdown_out, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    if not args.quiet:
        print(markdown)

    blocking = plan.stats.get("blocking_conflicts", 0)
    print(
        f"\n[planner] case={plan.case_id} feasibility={plan.feasibility} "
        f"tasks={plan.stats.get('tasks')} blocking_conflicts={blocking}"
    )
    return 1 if plan.feasibility == "infeasible" else 0


if __name__ == "__main__":
    raise SystemExit(main())
