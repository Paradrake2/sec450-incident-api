"""Append-only source evidence, separate from incident and review state.

Only an explicit database migration creates these private tables. Importing a
report retains its first raw representation and records every later fetch as a
separate observation; it does not decide which source version is current.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from team102_contract import ReportFetch, canonical_report, parse_report, report_url

API_SOURCE_SYSTEM = "team102-query-api"
FIXTURE_SOURCE_SYSTEM = "team102-query-api-fixture"
SNAPSHOT_UNIQUE_CONSTRAINT = "uq_source_report_snapshot_semantic"


class ReportImportBase(DeclarativeBase):
    """Metadata deliberately excluded from the application's startup create_all."""


class SourceReportSnapshot(ReportImportBase):
    __tablename__ = "source_report_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "source_report_id", "canonical_sha256",
            name=SNAPSHOT_UNIQUE_CONSTRAINT,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    source_report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceReportObservation(ReportImportBase):
    __tablename__ = "source_report_observations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("source_report_snapshots.id"), nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_method: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


@dataclass(frozen=True)
class ImportReceipt:
    snapshot_id: str
    observation_id: str
    created: bool
    source_report_id: int
    canonical_sha256: str


def _validate_provenance(fetch: ReportFetch) -> str:
    if (
        not isinstance(fetch.fetched_at, datetime)
        or fetch.fetched_at.tzinfo is None
        or fetch.fetched_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("fetched_at must be an aware UTC datetime")
    if fetch.source_url != report_url(fetch.report_id):
        raise ValueError("source_url must be the exact source report URL")
    if not isinstance(fetch.retrieval_method, str) or fetch.retrieval_method not in {"api", "fixture"}:
        raise ValueError("retrieval_method must be api or fixture")
    if fetch.request_id is not None and (
        not isinstance(fetch.request_id, str)
        or len(fetch.request_id) > 200
        or "\r" in fetch.request_id
        or "\n" in fetch.request_id
    ):
        raise ValueError("request_id must be at most 200 characters without line breaks")
    return API_SOURCE_SYSTEM if fetch.retrieval_method == "api" else FIXTURE_SOURCE_SYSTEM


def import_report(database_engine: Engine, fetch: ReportFetch) -> ImportReceipt:
    """Atomically retain a semantic snapshot and this fetch's provenance.

    The conflict insert is the first database statement. On SQLite this starts
    a real outer write transaction before any savepoint could be released, so
    an observation failure also rolls back a newly inserted snapshot.
    """
    # Reparse the original bytes at the persistence boundary rather than
    # trusting any caller-provided parsed representation or digest.
    document = parse_report(fetch.raw_content, fetch.report_id)
    source_system = _validate_provenance(fetch)
    semantic_digest = sha256(canonical_report(document)).hexdigest()
    raw_digest = sha256(fetch.raw_content).hexdigest()
    if database_engine.dialect.name == "postgresql":
        insert = postgres_insert
    elif database_engine.dialect.name == "sqlite":
        insert = sqlite_insert
    else:
        raise ValueError("Source report imports require PostgreSQL or SQLite")

    candidate_id = str(uuid4())
    observation_id = str(uuid4())
    source_report_id = str(fetch.report_id)
    statement = insert(SourceReportSnapshot).values(
        id=candidate_id,
        source_system=source_system,
        source_report_id=source_report_id,
        canonical_sha256=semantic_digest,
        raw_sha256=raw_digest,
        original_content=fetch.raw_content,
        imported_at=datetime.now(UTC),
    ).on_conflict_do_nothing(index_elements=[
        "source_system", "source_report_id", "canonical_sha256",
    ])

    with Session(database_engine) as session, session.begin():
        session.execute(statement)
        snapshot_id = session.scalar(select(SourceReportSnapshot.id).where(
            SourceReportSnapshot.source_system == source_system,
            SourceReportSnapshot.source_report_id == source_report_id,
            SourceReportSnapshot.canonical_sha256 == semantic_digest,
        ))
        if snapshot_id is None:
            raise RuntimeError("Source report snapshot could not be retained")
        session.add(SourceReportObservation(
            id=observation_id,
            snapshot_id=snapshot_id,
            fetched_at=fetch.fetched_at.astimezone(UTC),
            source_url=fetch.source_url,
            request_id=fetch.request_id,
            retrieval_method=fetch.retrieval_method,
            raw_sha256=raw_digest,
        ))
        receipt = ImportReceipt(
            snapshot_id=snapshot_id,
            observation_id=observation_id,
            created=snapshot_id == candidate_id,
            source_report_id=fetch.report_id,
            canonical_sha256=semantic_digest,
        )
    # A failed flush or commit propagates without issuing a success receipt.
    return receipt
