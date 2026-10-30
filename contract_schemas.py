"""Documented responses for the Phase 2 common API (not a change to wire JSON)."""
from typing import Annotated
from typing import Literal

from pydantic import BaseModel, Field


DateTimeString = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


IncidentState = Literal[
    "RECEIVED", "UNDER_REVIEW", "POTENTIALLY_MATERIAL", "NOT_MATERIAL",
    "MATERIAL_CONFIRMED", "CLOSED",
]


class ErrorResponse(BaseModel):
    error: str
    message: str
    field: str | None = None


class AssessmentResponse(BaseModel):
    potentiallyMaterial: bool
    explanation: str = Field(min_length=1)
    indicators: list[str] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    decision: Literal["MATERIAL", "NOT_MATERIAL"]
    reviewer: str
    rationale: str | None = None
    reviewedAt: DateTimeString


class IncidentResponse(BaseModel):
    id: str
    title: str
    log: str
    occurredAt: DateTimeString
    createdAt: DateTimeString
    source: str | None = None
    state: IncidentState
    assessment: AssessmentResponse | None = None
    review: ReviewResponse | None = None
    relatedIncidentIds: list[str]


class DisclosureResponse(BaseModel):
    incidentId: str
    draft: str = Field(min_length=1)
    generatedAt: DateTimeString
    final: Literal[False]


class ReviewHistoryResponse(ReviewResponse):
    id: str


class DisclosureHistoryResponse(DisclosureResponse):
    id: str
