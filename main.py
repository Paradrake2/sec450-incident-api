"""SEC450 incident evidence API (educational, synthetic data only)."""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from json import dumps, loads

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, select, delete
from sqlalchemy.orm import DeclarativeBase, Session

from triage import assess_log

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
    return dt.isoformat()

def serialize(row):
    return {"id": row.id, "title": row.title, "log": row.log,
            "occurredAt": stamp(row.occurred_at), "createdAt": stamp(row.created_at),
            "source": row.source, "state": row.state,
            "assessment": loads(row.assessment) if row.assessment else None,
            "review": loads(row.review) if row.review else None,
            "relatedIncidentIds": loads(row.related)}

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

def backfill_legacy_reviews():
    """Preserve the latest existing review when upgrading a Phase 3 database.

    Previous overwritten decisions cannot be reconstructed. This is idempotent
    for normal single-worker startup; new reviews are inserted transactionally.
    """
    with Session(engine) as session:
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
            session.add(ReviewRow(
                id=str(uuid4()), incident_id=incident.id, decision=snapshot["decision"],
                reviewer=snapshot["reviewer"], rationale=snapshot.get("rationale"),
                reviewed_at=reviewed_at,
            ))
            count += 1
        session.commit()
        if count:
            log.info("backfilled %s legacy review snapshots", count)


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    backfill_legacy_reviews()
    log.info("service ready database=%s", "postgresql" if DB_URL.startswith("postgresql") else "sqlite")
    yield

app = FastAPI(title="SEC Cybersecurity Incident Testing API", version="1.0.0", lifespan=lifespan)

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

@app.middleware("http")
async def require_json(request, call_next):
    if request.method == "POST" and request.url.path.startswith("/api/v1/incidents") and (request.url.path == "/api/v1/incidents" or request.url.path.endswith(("/review", "/related"))):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse(status_code=415, content={"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Use application/json"})
    return await call_next(request)

@app.get("/health")
@app.get("/api/v1/health")
def health():
    return {"status": "ok"}

@app.post("/api/v1/test/reset", status_code=204)
def reset():
    # WARNING: Spec requires unauthenticated reset. Deploy only with synthetic demo data.
    with Session(engine) as session:
        session.execute(delete(ReviewRow))
        session.execute(delete(IncidentRow))
        session.commit()
    return Response(status_code=204)

@app.post("/api/v1/incidents", status_code=201)
def create_incident(data: CreateIncident):
    if not data.title.strip() or not data.log.strip():
        error("INVALID_REQUEST", "title and log must not be blank", 400)
    now = datetime.now(timezone.utc)
    row = IncidentRow(id=str(uuid4()), title=data.title, log=data.log, occurred_at=data.occurredAt,
                      created_at=now, source=data.source, state="RECEIVED", related="[]")
    with Session(engine) as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return serialize(row)

STATES = {"RECEIVED", "UNDER_REVIEW", "POTENTIALLY_MATERIAL", "NOT_MATERIAL", "MATERIAL_CONFIRMED", "CLOSED"}

@app.get("/api/v1/incidents")
def list_incidents(state: str | None = None):
    if state is not None and state not in STATES:
        error("INVALID_REQUEST", "Unknown incident state", 400, "state")
    with Session(engine) as session:
        query = select(IncidentRow).order_by(IncidentRow.created_at, IncidentRow.id)
        if state is not None:
            query = query.where(IncidentRow.state == state)
        return [serialize(row) for row in session.scalars(query)]

@app.get("/api/v1/incidents/{incident_id}")
def get_one(incident_id: str):
    with Session(engine) as session:
        return serialize(get_incident(session, incident_id))



@app.post("/api/v1/incidents/{incident_id}/assessment")
def assess(incident_id: str):
    with Session(engine) as session:
        row = get_incident(session, incident_id)
        if row.state != "RECEIVED":
            error("INVALID_STATE", "Incident cannot be assessed in its current state", 409)
        value = assess_log(row.log)
        row.assessment = dumps(value)
        row.state = "POTENTIALLY_MATERIAL" if value["potentiallyMaterial"] else "UNDER_REVIEW"
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


@app.post("/api/v1/incidents/{incident_id}/review")
def review(incident_id: str, data: ReviewRequest):
    if not data.reviewer.strip():
        error("INVALID_REQUEST", "reviewer must not be blank", 400, "reviewer")
    with Session(engine) as session:
        row = get_incident(session, incident_id)
        if data.decision not in ALLOWED_REVIEW_DECISIONS.get(row.state, ()):
            error("INVALID_STATE", "Review is not permitted in current state", 409)

        reviewed_at = datetime.now(timezone.utc)
        snapshot = {"decision": data.decision, "reviewer": data.reviewer,
                    "rationale": data.rationale, "reviewedAt": stamp(reviewed_at)}
        session.add(ReviewRow(id=str(uuid4()), incident_id=row.id, decision=data.decision,
                              reviewer=data.reviewer, rationale=data.rationale, reviewed_at=reviewed_at))
        row.review = dumps(snapshot)  # Backward-compatible most recent review.
        row.state = "MATERIAL_CONFIRMED" if data.decision == "MATERIAL" else "NOT_MATERIAL"
        session.commit()  # Record the decision and new state in one transaction.
        return serialize(row)


@app.get("/api/v1/incidents/{incident_id}/reviews")
def list_reviews(incident_id: str):
    """Optional read-only history endpoint; original Incident response is unchanged."""
    with Session(engine) as session:
        get_incident(session, incident_id)
        reviews = session.scalars(
            select(ReviewRow).where(ReviewRow.incident_id == incident_id)
            .order_by(ReviewRow.reviewed_at, ReviewRow.id)
        )
        return [serialize_review(item) for item in reviews]

@app.post("/api/v1/incidents/{incident_id}/related")
def relate(incident_id: str, data: LinkRequest):
    with Session(engine) as session:
        row = get_incident(session, incident_id)
        other = get_incident(session, data.relatedIncidentId)
        if row.state == "CLOSED" or other.state == "CLOSED":
            error("INVALID_STATE", "Closed incidents cannot be modified", 409)
        if row.id == other.id or other.id in loads(row.related):
            error("DUPLICATE_RELATION", "Relation already exists or relates incident to itself", 409)
        row.related = dumps([*loads(row.related), other.id])
        other.related = dumps([*loads(other.related), row.id])
        session.commit()
        return serialize(row)

@app.post("/api/v1/incidents/{incident_id}/disclosure")
def disclosure(incident_id: str):
    with Session(engine) as session:
        row = get_incident(session, incident_id)
        if row.state != "MATERIAL_CONFIRMED":
            error("INVALID_STATE", "A material human review is required before drafting", 409)
        # Explicit non-final template. No automatic legal analysis or filing.
        draft = (f"DRAFT FOR LEGAL REVIEW — NOT AN SEC FILING\nIncident: {row.title}\n"
                 f"Known incident timing: {stamp(row.occurred_at)}\n"
                 f"Reported facts (unverified): {row.log[:2000]}\n"
                 "Nature, scope, and material impact: To be verified and completed by legal counsel.\n"
                 "This draft must be reviewed and approved by authorized personnel.")
        return {"incidentId": row.id, "draft": draft, "generatedAt": stamp(datetime.now(timezone.utc)), "final": False}
