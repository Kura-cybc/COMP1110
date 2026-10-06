"""Shared hand-off contract for the research and timeline modules."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


ProgrammeType = Literal["summer_school", "winter_school", "exchange"]
FactState = Literal["verified", "needs_review", "historical", "conflicting", "unknown"]
DeadlineKind = Literal["fixed", "window", "rolling", "relative", "unknown", "none"]
StepCategory = Literal[
    "host_application", "hku_application", "nomination", "offer_acceptance",
    "funding", "visa", "credit_transfer", "insurance", "accommodation", "other",
]


class ResearchRequest(BaseModel):
    language: Literal["zh", "en"] = "zh"
    destination: str = Field(min_length=2, max_length=100)
    start_date: date
    end_date: date
    programme_type: ProgrammeType
    major: str = Field(default="", max_length=120)
    year_of_study: int | None = Field(default=None, ge=1, le=8)
    interests: str = Field(default="", max_length=300)
    nationality: str = Field(default="", max_length=100)
    residence: str = Field(default="Hong Kong", max_length=100)
    budget: str = Field(default="", max_length=100)
    programme_url: HttpUrl | None = None
    max_programmes: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def check_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期必须不早于开始日期")
        return self


class Evidence(BaseModel):
    source_id: str
    quote: str = Field(max_length=400)


class Fact(BaseModel):
    value: str | None = None
    state: FactState = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class Deadline(BaseModel):
    kind: DeadlineKind = "unknown"
    date: str | None = None
    raw: str | None = None
    timezone: str | None = None
    trigger: str | None = None
    state: FactState = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class DocumentRequirement(BaseModel):
    name: str
    detail: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    state: FactState = "unknown"


class Step(BaseModel):
    id: str
    category: StepCategory
    title: str
    action: Fact = Field(default_factory=Fact)
    channel: Fact = Field(default_factory=Fact)
    deadline: Deadline = Field(default_factory=Deadline)
    documents: list[DocumentRequirement] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    applies_if: str | None = None
    notes: list[str] = Field(default_factory=list)


class Programme(BaseModel):
    id: str
    name: str
    official_url: str | None = None
    programme_type: ProgrammeType
    destination: str
    dates: Fact = Field(default_factory=Fact)
    eligibility: list[Fact] = Field(default_factory=list)
    costs: list[Fact] = Field(default_factory=list)
    past_cases: list[Fact] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)


class Source(BaseModel):
    id: str
    url: str
    title: str
    publisher_type: Literal["hku", "host", "government", "social", "other", "knowledge_base"]
    retrieved_at: datetime
    read_status: Literal["read", "unavailable", "login_required"]
    note: str | None = None


class DiscoveryClue(BaseModel):
    """A search result is a lead, not a verified application requirement."""

    url: str
    title: str
    snippet: str = ""
    platform: str
    query: str


class SearchAttempt(BaseModel):
    query: str
    purpose: Literal["social_discovery", "official_discovery", "gap_followup"]
    status: Literal["results", "empty", "error"]
    result_count: int = 0
    note: str | None = None


class KnowledgeSnippet(BaseModel):
    id: str
    title: str
    text: str
    links: dict[str, str] = Field(default_factory=dict)


class ResearchResult(BaseModel):
    schema_version: str = "1.0"
    request: ResearchRequest
    generated_at: datetime
    provider: str
    programmes: list[Programme] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    discovery_clues: list[DiscoveryClue] = Field(default_factory=list)
    search_log: list[SearchAttempt] = Field(default_factory=list)
    knowledge_matches: list[KnowledgeSnippet] = Field(default_factory=list)


class Draft(BaseModel):
    """LLM output. Evidence and factual fields are re-checked before hand-off."""

    programmes: list[Programme] = Field(default_factory=list)


class TranslationDraft(BaseModel):
    """Translate display text only; evidence, URLs and machine states are preserved."""

    translations: list[str]
