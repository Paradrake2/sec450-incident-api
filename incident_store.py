"""Incident persistence using caller-owned sessions; never selects a database."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text,
    select, text,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Session

from incident_identity import (
    TEAM102_SOURCE_INDEX_NAME, TEAM102_SOURCE_PREDICATE, is_team102_source,
)
from request_models import CreateIncident


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


team102_source_index = Index(
    TEAM102_SOURCE_INDEX_NAME, IncidentRow.source, unique=True,
    sqlite_where=text(TEAM102_SOURCE_PREDICATE),
    postgresql_where=text(TEAM102_SOURCE_PREDICATE),
)


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


class InvalidIncidentError(ValueError):
    """A payload cannot be saved as an incident."""


def save_incident(session: Session, data: CreateIncident):
    """Stage an incident in the caller's transaction; return (row, created).

    The conflict insert serializes concurrent report retries in the database.
    Ordinary sources are descriptive labels and do not identify an incident.
    """
    if not data.title.strip() or not data.log.strip():
        raise InvalidIncidentError("title and log must not be blank")
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
    if is_team102_source(data.source):
        dialect = session.get_bind().dialect.name
        insert = {"postgresql": postgres_insert, "sqlite": sqlite_insert}.get(dialect)
        if insert is None:
            raise ValueError("Team 102 incident imports require PostgreSQL or SQLite")
        candidate_id = row.id
        session.execute(insert(IncidentRow).values(
            id=row.id, title=row.title, log=row.log, occurred_at=row.occurred_at,
            created_at=row.created_at, source=row.source, state=row.state, related=row.related,
        ).on_conflict_do_nothing(
            index_elements=["source"], index_where=text(TEAM102_SOURCE_PREDICATE),
        ))
        row = session.scalar(select(IncidentRow).where(IncidentRow.source == data.source))
        if row is None:
            raise RuntimeError("Team 102 incident could not be retained")
        return row, row.id == candidate_id
    session.add(row)
    session.flush()
    return row, True
