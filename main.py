"""SEC450 incident evidence API (educational, synthetic data only)."""
import logging
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from json import dumps, loads, JSONDecodeError

from fastapi import Depends, FastAPI, Request, HTTPException, Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, CheckConstraint, Boolean, or_, select, delete, update
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from triage import assess_log
from disclosure_templates import build_draft
from contract_schemas import (
    IncidentResponse, AssessmentResponse, DisclosureResponse, ErrorResponse,
    ReviewHistoryResponse, DisclosureHistoryResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sec450")

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./incidents.db")
if DB_URL.startswith("postgres://"):
    DB_URL = "postgresql://" + DB_URL[len("postgres://"):]
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})

class Base(DeclarativeBase):
    pass

class IncidentRow(Base):
    __tablename__ = "incidents"
    id = Column(String(36), primary_key=True)
    title = Column(String(200), nullable=False)
    log = Column(Text, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(100), nullable=True)
    state = Column(String(32), nullable=False, default="RECEIVED")
    assessment = Column(Text, nullable=True)
    review = Column(Text, nullable=True)
    related = Column(Text, nullable=False, default="[]")

class ReviewRow(Base):
    """Append-only review decisions; IncidentRow.review stays the latest snapshot."""
    __tablename__ = "incident_reviews"
    id = Column(String(36), primary_key=True)
    incident_id = Column(String(36), ForeignKey("incidents.id"), nullable=False, index=True)
    decision = Column(String(20), nullable=False)
    reviewer = Column(Text, nullable=False)
    rationale = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)

class RelationRow(Base):
    """Each undirected link is stored exactly once, using canonical ID order."""
    __tablename__ = "incident_relations"
    incident_a_id = Column(String(36), ForeignKey("incidents.id"), primary_key=True)
    incident_b_id = Column(String(36), ForeignKey("incidents.id"), primary_key=True)
    __table_args__ = (
        CheckConstraint("incident_a_id < incident_b_id", name="ck_relation_canonical_order"),
    )


class DisclosureRow(Base):
    """Immutable generated disclosure versions, never final SEC filings."""
    __tablename__ = "disclosure_drafts"
    id = Column(String(36), primary_key=True)
    incident_id = Column(String(36), ForeignKey("incidents.id"), nullable=False, index=True)
    draft = Column(Text, nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    final = Column(Boolean, nullable=False, default=False)


class CreateIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    log: str = Field(min_length=1)
    occurredAt: datetime
    source: str | None = Field(default=None, max_length=100)

class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["MATERIAL", "NOT_MATERIAL"]
    reviewer: str = Field(min_length=1)
    rationale: str | None = None

class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relatedIncidentId: str = Field(min_length=1)


def stamp(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def related_ids(session, incident_id):
    """Read authoritative link records rather than legacy JSON snapshots."""
    pairs = session.execute(
        select(RelationRow.incident_a_id, RelationRow.incident_b_id).where(
            or_(RelationRow.incident_a_id == incident_id,
                RelationRow.incident_b_id == incident_id)
        )
    ).all()
    return sorted(b if a == incident_id else a for a, b in pairs)


def serialize(row, session):
    return {"id": row.id, "title": row.title, "log": row.log,
            "occurredAt": stamp(row.occurred_at), "createdAt": stamp(row.created_at),
            "source": row.source, "state": row.state,
            "assessment": loads(row.assessment) if row.assessment else None,
            "review": loads(row.review) if row.review else None,
            "relatedIncidentIds": related_ids(session, row.id)}

def error(code, message, status, field=None):
    body = {"error": code, "message": message}
    if field is not None:
        body["field"] = field
    raise HTTPException(status_code=status, detail=body)

def get_incident(session, incident_id):
    obj = session.get(IncidentRow, incident_id)
    if obj is None:
        error("INCIDENT_NOT_FOUND", "No incident exists with the supplied identifier", 404)
    return obj

def backfill_legacy_reviews(database_engine: Engine | None = None):
    """Preserve the latest existing review when upgrading a Phase 3 database.

    Previous overwritten decisions cannot be reconstructed. This is idempotent
    for normal single-worker startup; new reviews are inserted transactionally.
    """
    with Session(engine if database_engine is None else database_engine) as session:
        legacy_incidents = session.scalars(select(IncidentRow).where(IncidentRow.review.isnot(None)))
        count = 0
        for incident in legacy_incidents:
            existing = session.scalar(
                select(ReviewRow.id).where(ReviewRow.incident_id == incident.id).limit(1)
            )
            if existing is not None:
                continue
            snapshot = loads(incident.review)
            reviewed_at = datetime.fromisoformat(snapshot["reviewedAt"].replace("Z", "+00:00"))
            if reviewed_at.tzinfo is None:
                reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
            reviewed_at = reviewed_at.astimezone(timezone.utc)
            session.add(ReviewRow(
                id=str(uuid4()), incident_id=incident.id, decision=snapshot["decision"],
                reviewer=snapshot["reviewer"], rationale=snapshot.get("rationale"),
                reviewed_at=reviewed_at,
            ))
            count += 1
        session.commit()
        if count:
            log.info("backfilled %s legacy review snapshots", count)


def backfill_legacy_relations(database_engine: Engine | None = None):
    """One-time compatible import from old symmetric or asymmetric JSON lists.

    Existing serialized snapshots remain untouched. A valid legacy link is
    imported once even if both incident rows reference each other. Normal
    application reads now use the normalized relationship table exclusively.
    """
    with Session(engine if database_engine is None else database_engine) as session:
        incidents = session.scalars(select(IncidentRow)).all()
        valid_ids = {row.id for row in incidents}
        existing_pairs = {
            (a, b) for a, b in session.execute(
                select(RelationRow.incident_a_id, RelationRow.incident_b_id)
            )
        }
        added = 0
        for incident in incidents:
            try:
                legacy_ids = loads(incident.related or "[]")
            except (TypeError, JSONDecodeError):
                log.warning("skipping invalid legacy relations for incident_id=%s", incident.id)
                continue
            if not isinstance(legacy_ids, list):
                log.warning("skipping non-array legacy relations for incident_id=%s", incident.id)
                continue
            for other_id in legacy_ids:
                if not isinstance(other_id, str) or other_id == incident.id or other_id not in valid_ids:
                    log.warning("skipping dangling or invalid legacy relation for incident_id=%s", incident.id)
                    continue
                pair = tuple(sorted((incident.id, other_id)))
                if pair not in existing_pairs:
                    session.add(RelationRow(incident_a_id=pair[0], incident_b_id=pair[1]))
                    existing_pairs.add(pair)
                    added += 1
        session.commit()
        if added:
            log.info("backfilled %s legacy incident relations", added)


def initialize_database(database_engine: Engine) -> None:
    """Prepare only the explicitly selected store, including legacy imports."""
    Base.metadata.create_all(database_engine)
    backfill_legacy_reviews(database_engine)
    backfill_legacy_relations(database_engine)


@asynccontextmanager
async def lifespan(app):
    database_engine = app.state.database_engine
    initialize_database(database_engine)
    log.info("service ready database=%s", database_engine.dialect.name)
    yield

app = FastAPI(title="SEC Cybersecurity Incident Testing API", version="1.0.0", lifespan=lifespan)
app.state.database_engine = engine


def get_engine(request: Request) -> Engine:
    """Select server-configured storage; request data cannot choose a database."""
    return request.app.state.database_engine


def get_session(database_engine: Engine = Depends(get_engine)) -> Iterator[Session]:
    """One session per request; close rolls back any uncommitted work.

    Routes own their commits so changes fail before a response is returned.
    Lifespan uses the app's engine directly; dependency overrides affect requests
    only and must prepare any alternate store explicitly.
    """
    with Session(database_engine) as session:
        yield session

# Declared responses keep Swagger and downstream schema checks aligned with
# the existing structured-error handler (which returns 400, not FastAPI's 422).
ERROR_400 = {400: {"model": ErrorResponse, "description": "Invalid request"}}
ERROR_404 = {404: {"model": ErrorResponse, "description": "Incident not found"}}
ERROR_409 = {409: {"model": ErrorResponse, "description": "Invalid state or duplicate relation"}}
ERROR_415 = {415: {"model": ErrorResponse, "description": "Unsupported content type"}}
ERROR_500 = {500: {"model": ErrorResponse, "description": "Unexpected server-side error"}}


@app.middleware("http")
async def request_logging(request, call_next):
    response = await call_next(request)
    log.info("method=%s path=%s status=%s", request.method, request.url.path, response.status_code)
    return response

@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse(status_code=exc.status_code, content=exc.detail if isinstance(exc.detail, dict) else {"error": "HTTP_ERROR", "message": str(exc.detail)})

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    first = exc.errors()[0]
    field = str(first.get("loc", [""])[-1])
    return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST", "message": first.get("msg", "Invalid request"), "field": field})

@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # Keep internal exception details out of the public synthetic-data API.
    log.exception("unhandled request error method=%s path=%s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={
        "error": "INTERNAL_ERROR", "message": "Unexpected server-side failure",
    })

@app.middleware("http")
async def require_json(request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/v1/incidents") and (request.url.path == "/api/v1/incidents" or request.url.path.endswith(("/review", "/related"))):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse(status_code=415, content={"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Use application/json"})
    return await call_next(request)

@app.get("/health", responses={503: {"description": "Service temporarily unavailable"}})
@app.get("/api/v1/health", responses={503: {"description": "Service temporarily unavailable"}})
def health():
    return {"status": "ok"}

@app.post("/api/v1/test/reset", status_code=204, responses=ERROR_500)
def reset(session: Session = Depends(get_session)):
    # WARNING: Spec requires unauthenticated reset. Deploy only with synthetic demo data.
    session.execute(delete(DisclosureRow))
    session.execute(delete(RelationRow))
    session.execute(delete(ReviewRow))
    session.execute(delete(IncidentRow))
    session.commit()
    return Response(status_code=204)

@app.post("/api/v1/incidents", status_code=201, response_model=IncidentResponse, responses={**ERROR_400, **ERROR_415, **ERROR_500})
def create_incident(data: CreateIncident, session: Session = Depends(get_session)):
    if not data.title.strip() or not data.log.strip():
        error("INVALID_REQUEST", "title and log must not be blank", 400)
    now = datetime.now(timezone.utc)
    # SQLite drops timezone offsets while PostgreSQL normalizes the instant.
    # Normalize before persistence so both stores retain the same UTC instant.
    occurred_at = data.occurredAt
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    else:
        occurred_at = occurred_at.astimezone(timezone.utc)
    row = IncidentRow(id=str(uuid4()), title=data.title, log=data.log, occurred_at=occurred_at,
                      created_at=now, source=data.source, state="RECEIVED", related="[]")
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize(row, session)

STATES = {"RECEIVED", "UNDER_REVIEW", "POTENTIALLY_MATERIAL", "NOT_MATERIAL", "MATERIAL_CONFIRMED", "CLOSED"}

@app.get("/api/v1/incidents", response_model=list[IncidentResponse], responses={**ERROR_400, **ERROR_500})
def list_incidents(state: str | None = None, session: Session = Depends(get_session)):
    if state is not None and state not in STATES:
        error("INVALID_REQUEST", "Unknown incident state", 400, "state")
    query = select(IncidentRow).order_by(IncidentRow.created_at, IncidentRow.id)
    if state is not None:
        query = query.where(IncidentRow.state == state)
    return [serialize(row, session) for row in session.scalars(query)]

@app.get("/api/v1/incidents/{incidentId}", response_model=IncidentResponse, responses={**ERROR_404, **ERROR_500})
def get_one(incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    return serialize(get_incident(session, incident_id), session)

@app.post("/api/v1/incidents/{incidentId}/assessment", response_model=AssessmentResponse, responses={**ERROR_404, **ERROR_409, **ERROR_500})
def assess(incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    row = get_incident(session, incident_id)
    if row.state != "RECEIVED":
        error("INVALID_STATE", "Incident cannot be assessed in its current state", 409)
    value = assess_log(row.log)
    result = session.execute(
        update(IncidentRow).where(
            IncidentRow.id == row.id, IncidentRow.state == "RECEIVED",
        ).values(
            assessment=dumps(value),
            state="POTENTIALLY_MATERIAL" if value["potentiallyMaterial"] else "UNDER_REVIEW",
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        error("INVALID_STATE", "Incident cannot be assessed in its current state", 409)
    session.commit()
    return value

# Phase 2 state machine: assessments are advisory; human review is final.
# NOT_MATERIAL may later escalate to MATERIAL. Confirmed material cannot be
# downgraded and a closed or unassessed incident cannot be reviewed.
ALLOWED_REVIEW_DECISIONS = {
    "UNDER_REVIEW": frozenset({"MATERIAL", "NOT_MATERIAL"}),
    "POTENTIALLY_MATERIAL": frozenset({"MATERIAL", "NOT_MATERIAL"}),
    "NOT_MATERIAL": frozenset({"MATERIAL"}),
}


def serialize_review(row):
    return {"id": row.id, "decision": row.decision, "reviewer": row.reviewer,
            "rationale": row.rationale, "reviewedAt": stamp(row.reviewed_at)}


@app.post("/api/v1/incidents/{incidentId}/review", response_model=IncidentResponse, responses={**ERROR_400, **ERROR_404, **ERROR_409, **ERROR_415, **ERROR_500})
def review(data: ReviewRequest, incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    if not data.reviewer.strip():
        error("INVALID_REQUEST", "reviewer must not be blank", 400, "reviewer")
    row = get_incident(session, incident_id)
    if data.decision not in ALLOWED_REVIEW_DECISIONS.get(row.state, ()):
        error("INVALID_STATE", "Review is not permitted in current state", 409)

    reviewed_at = datetime.now(timezone.utc)
    snapshot = {"decision": data.decision, "reviewer": data.reviewer,
                "rationale": data.rationale, "reviewedAt": stamp(reviewed_at)}
    # Apply the transition only if the state read above is still current. On
    # PostgreSQL a competing update is rechecked after its transaction finishes.
    # The accepted decision and history insert share this transaction.
    result = session.execute(
        update(IncidentRow).where(
            IncidentRow.id == row.id, IncidentRow.state == row.state,
        ).values(
            review=dumps(snapshot),
            state="MATERIAL_CONFIRMED" if data.decision == "MATERIAL" else "NOT_MATERIAL",
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        error("INVALID_STATE", "Review is not permitted in current state", 409)
    session.add(ReviewRow(id=str(uuid4()), incident_id=row.id, decision=data.decision,
                          reviewer=data.reviewer, rationale=data.rationale, reviewed_at=reviewed_at))
    session.commit()  # Record the decision and new state in one transaction.
    return serialize(row, session)


@app.get("/api/v1/incidents/{incidentId}/reviews", response_model=list[ReviewHistoryResponse], responses={**ERROR_404, **ERROR_500})
def list_reviews(incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    """Optional read-only history endpoint; original Incident response is unchanged."""
    get_incident(session, incident_id)
    reviews = session.scalars(
        select(ReviewRow).where(ReviewRow.incident_id == incident_id)
        .order_by(ReviewRow.reviewed_at, ReviewRow.id)
    )
    return [serialize_review(item) for item in reviews]

@app.post("/api/v1/incidents/{incidentId}/related", response_model=IncidentResponse, responses={**ERROR_400, **ERROR_404, **ERROR_409, **ERROR_415, **ERROR_500})
def relate(data: LinkRequest, incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    row = get_incident(session, incident_id)
    other = get_incident(session, data.relatedIncidentId)
    if row.state == "CLOSED" or other.state == "CLOSED":
        error("INVALID_STATE", "Closed incidents cannot be modified", 409)
    if row.id == other.id:
        error("DUPLICATE_RELATION", "Relation already exists or relates incident to itself", 409)
    a, b = sorted((row.id, other.id))
    if session.get(RelationRow, (a, b)) is not None:
        error("DUPLICATE_RELATION", "Relation already exists or relates incident to itself", 409)
    session.add(RelationRow(incident_a_id=a, incident_b_id=b))
    try:
        session.commit()
    except IntegrityError:
        # Unique composite key protects against simultaneous duplicate links.
        session.rollback()
        error("DUPLICATE_RELATION", "Relation already exists or relates incident to itself", 409)
    return serialize(row, session)

def serialize_disclosure(row):
    """Preserve the shared four-field DisclosureDraft response format."""
    return {"incidentId": row.incident_id, "draft": row.draft,
            "generatedAt": stamp(row.generated_at), "final": False}


@app.post("/api/v1/incidents/{incidentId}/disclosure", response_model=DisclosureResponse, responses={**ERROR_404, **ERROR_409, **ERROR_500})
def disclosure(incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    row = get_incident(session, incident_id)
    if row.state != "MATERIAL_CONFIRMED" or not row.review:
        error("INVALID_STATE", "A material human review is required before drafting", 409)
    # Human review is submitted evidence, not verified identity or legal approval.
    review_data = loads(row.review)
    if review_data.get("decision") != "MATERIAL":
        error("INVALID_STATE", "A material human review is required before drafting", 409)
    draft = build_draft(
        incident_id=row.id, title=row.title, log=row.log,
        occurred_at=stamp(row.occurred_at), created_at=stamp(row.created_at),
        source=row.source, review=review_data,
    )
    record = DisclosureRow(
        id=str(uuid4()), incident_id=row.id, draft=draft,
        generated_at=datetime.now(timezone.utc), final=False,
    )
    session.add(record)
    session.commit()
    return serialize_disclosure(record)


@app.get("/api/v1/incidents/{incidentId}/disclosures", response_model=list[DisclosureHistoryResponse], responses={**ERROR_404, **ERROR_500})
def list_disclosures(incident_id: str = PathParam(alias="incidentId"), session: Session = Depends(get_session)):
    """Optional history endpoint; not part of the common pod contract."""
    get_incident(session, incident_id)
    drafts = session.scalars(
        select(DisclosureRow).where(DisclosureRow.incident_id == incident_id)
        .order_by(DisclosureRow.generated_at, DisclosureRow.id)
    )
    return [{"id": d.id, **serialize_disclosure(d)} for d in drafts]


# FastAPI automatically documents validation as 422 even with a custom 400
# handler. The API has never returned 422 for these request-model errors.
# Remove that misleading default from generated OpenAPI without affecting
# runtime request/response behavior.
def contract_openapi():
    if app.openapi_schema is None:
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        for path in schema["paths"].values():
            for operation in path.values():
                if isinstance(operation, dict) and "responses" in operation:
                    operation["responses"].pop("422", None)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = contract_openapi
