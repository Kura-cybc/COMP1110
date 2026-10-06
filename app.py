"""Local web UI and JSON API for the research and planning modules."""

import os
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from models import ResearchRequest, ResearchResult
from knowledge_base import KNOWLEDGE_PATH, retrieve_knowledge
from research import research

from planner.schema import PlanRequest, load_plan_request
from planner.timeline import build_plan, render_markdown as render_plan_markdown


ROOT = Path(__file__).resolve().parent
PLANNER_CASE_DIR = ROOT / "planner" / "cases"
app = FastAPI(title="ExchangePrep Research + Planner Demo", version="0.3.0")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/planner")
def planner_page():
    """Readable view of the dependency-modelling and timeline output."""

    return FileResponse(ROOT / "static" / "planner.html")


@app.get("/api/config")
def config():
    return {
        "llm_provider": os.getenv("EXCHANGEPREP_LLM_PROVIDER", os.getenv("PATHLOOM_LLM_PROVIDER", "openai")),
        "search_provider": os.getenv("EXCHANGEPREP_SEARCH_PROVIDER", os.getenv("PATHLOOM_SEARCH_PROVIDER", "auto")),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "brave_configured": bool(os.getenv("BRAVE_API_KEY")),
        "knowledge_base_available": KNOWLEDGE_PATH.is_file(),
    }


@app.get("/api/knowledge-base")
def search_knowledge_base(query: str = Query(min_length=1, max_length=300)):
    try:
        return {"matches": retrieve_knowledge(query), "document_url": "/api/knowledge-base/document"}
    except OSError as exc:
        raise HTTPException(status_code=503, detail="知识库文件不可读") from exc


@app.get("/api/knowledge-base/document")
def knowledge_document():
    if not KNOWLEDGE_PATH.is_file():
        raise HTTPException(status_code=404, detail="知识库文件不存在")
    return FileResponse(KNOWLEDGE_PATH, media_type="text/markdown; charset=utf-8")


@app.get("/api/sample", response_model=ResearchResult)
def sample():
    return ResearchResult.model_validate_json((ROOT / "sample.json").read_text(encoding="utf-8"))


@app.post("/api/research", response_model=ResearchResult)
def run_research(request: ResearchRequest, provider: Literal["openai", "deepseek"] | None = None):
    try:
        return research(request, provider=provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Never return API keys, raw provider bodies, or user data in an error response.
        raise HTTPException(status_code=502, detail=f"外部搜索或模型请求失败：{type(exc).__name__}") from exc


# --------------------------------------------------------------------------- #
# Dependency modelling and timeline generation (planner module)
# --------------------------------------------------------------------------- #
def _case_files() -> list[Path]:
    if not PLANNER_CASE_DIR.is_dir():
        return []
    return sorted(PLANNER_CASE_DIR.glob("*.json"))


def _case_path(case_id: str) -> Path:
    """Resolve a curated case by file stem *or* by the ``case_id`` it declares."""

    for path in _case_files():
        if path.stem == case_id:
            return path
        try:
            if load_plan_request(path).case_id == case_id:
                return path
        except Exception:
            continue
    raise HTTPException(status_code=404, detail=f"未知的规划案例：{case_id}")


@app.get("/api/planner/cases")
def planner_cases():
    """List the curated planning cases shipped with the planner module."""

    cases = []
    for path in _case_files():
        try:
            request = load_plan_request(path)
        except Exception:  # a broken manifest must not break the listing
            cases.append({"case_id": path.stem, "readable": False})
            continue
        cases.append(
            {
                "case_id": request.case_id,
                "file": path.name,
                "label": request.case_label,
                "programme_id": request.programme_id,
                "tasks": len(request.tasks),
                "as_of": request.as_of.isoformat(),
                "readable": True,
            }
        )
    return {"cases": cases}


@app.get("/api/planner/plan/{case_id}")
def planner_plan(
    case_id: str,
    as_of: date | None = Query(default=None, description="覆盖规划基准日期 YYYY-MM-DD"),
    format: Literal["json", "markdown"] = Query(default="json"),
):
    """Build the ordered, dated plan for a curated case."""

    request = load_plan_request(_case_path(case_id))
    if as_of is not None:
        request = request.model_copy(update={"as_of": as_of})
    plan = build_plan(request)
    if format == "markdown":
        return {"case_id": plan.case_id, "markdown": render_plan_markdown(plan)}
    return plan


@app.post("/api/planner/plan")
def planner_plan_from_payload(payload: PlanRequest, format: Literal["json", "markdown"] = "json"):
    """Build a plan from a full planning manifest sent by another module."""

    try:
        plan = build_plan(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if format == "markdown":
        return {"case_id": plan.case_id, "markdown": render_plan_markdown(plan)}
    return plan


@app.get("/api/planner/schema")
def planner_schema():
    """Publish the planner JSON schema so the research module can match it."""

    return {
        "plan_schema_version": PlanRequest.model_fields["plan_schema_version"].default,
        "plan_request_schema": PlanRequest.model_json_schema(),
    }
