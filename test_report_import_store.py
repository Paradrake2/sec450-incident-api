"""Source import persistence checks against explicitly disposable stores."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from json import dumps, loads
from threading import Barrier

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from report_import_store import (
    API_SOURCE_SYSTEM,
    FIXTURE_SOURCE_SYSTEM,
    ReportImportBase,
    SourceReportObservation,
    SourceReportSnapshot,
    import_report,
)
from team102_contract import ReportFetch, SourceApiError, canonical_report, report_url
from testing_database import disposable_engine

FETCHED_AT = datetime(2026, 10, 31, 14, 15, 16, tzinfo=UTC)
RAW_REPORT = (
    b'{ "report_id": 42, "body": {"message": "Synthetic only"}, '
    b'"unknown": {"retained": [true, 7, "extra"]} }\n'
)


@pytest.fixture
def database(tmp_path):
    with disposable_engine(tmp_path) as engine:
        # Unit tests explicitly prepare only the private import metadata.
        # Application startup must not create these tables in production.
        ReportImportBase.metadata.create_all(engine)
        yield engine


def fetched(raw=RAW_REPORT, report_id=42, **overrides):
    return ReportFetch(
        report_id=report_id, raw_content=raw, fetched_at=FETCHED_AT,
        source_url=report_url(report_id), **overrides,
    )


def stored_rows(database, model):
    with database.connect() as connection:
        return [dict(row) for row in connection.execute(
            select(model.__table__).order_by(model.id)
        ).mappings()]


def as_utc(value):
    # SQLite's timestamp storage does not retain timezone metadata.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_original_bytes_unknown_fields_digests_and_provenance_are_preserved(database):
    before = datetime.now(UTC)
    fetch = fetched(request_id="synthetic-request-1")
    receipt = import_report(database, fetch)
    after = datetime.now(UTC)

    snapshots = stored_rows(database, SourceReportSnapshot)
    observations = stored_rows(database, SourceReportObservation)
    assert len(snapshots) == len(observations) == 1
    snapshot, observation = snapshots[0], observations[0]
    assert snapshot["id"] == receipt.snapshot_id
    assert snapshot["source_system"] == API_SOURCE_SYSTEM
    assert snapshot["source_report_id"] == "42"
    assert snapshot["original_content"] == RAW_REPORT
    assert loads(snapshot["original_content"])["unknown"] == {"retained": [True, 7, "extra"]}
    assert snapshot["raw_sha256"] == sha256(RAW_REPORT).hexdigest()
    expected = sha256(canonical_report(loads(RAW_REPORT))).hexdigest()
    assert snapshot["canonical_sha256"] == receipt.canonical_sha256 == expected
    assert before <= as_utc(snapshot["imported_at"]) <= after
    assert observation == {
        "id": receipt.observation_id, "snapshot_id": receipt.snapshot_id,
        "fetched_at": observation["fetched_at"], "source_url": report_url(42),
        "request_id": "synthetic-request-1", "retrieval_method": "api",
        "raw_sha256": sha256(RAW_REPORT).hexdigest(),
    }
    assert as_utc(observation["fetched_at"]) == FETCHED_AT
    assert receipt.created is True
    assert receipt.source_report_id == 42
    with pytest.raises(FrozenInstanceError):
        receipt.created = False


def test_layout_duplicates_keep_first_original_and_record_each_raw_fingerprint(database):
    first = import_report(database, fetched())
    original_snapshot = stored_rows(database, SourceReportSnapshot)
    rearranged = (
        b'{"unknown":{"retained":[true,7,"extra"]},'
        b'"body":{"message":"Synthetic only"},"report_id":42}'
    )
    second = import_report(database, replace(
        fetched(rearranged, request_id="second-fetch"),
        fetched_at=FETCHED_AT + timedelta(seconds=1),
    ))
    third = import_report(database, fetched())

    assert [first.created, second.created, third.created] == [True, False, False]
    assert first.snapshot_id == second.snapshot_id == third.snapshot_id
    assert len({first.observation_id, second.observation_id, third.observation_id}) == 3
    assert stored_rows(database, SourceReportSnapshot) == original_snapshot
    observations = stored_rows(database, SourceReportObservation)
    assert len(observations) == 3
    second_observation = next(row for row in observations if row["id"] == second.observation_id)
    assert second_observation["raw_sha256"] == sha256(rearranged).hexdigest()
    assert second_observation["raw_sha256"] != original_snapshot[0]["raw_sha256"]
    assert second_observation["request_id"] == "second-fetch"


def test_changed_content_creates_new_snapshot_and_never_rewrites_prior_versions(database):
    first = import_report(database, fetched())
    initial = stored_rows(database, SourceReportSnapshot)[0]
    changed = loads(RAW_REPORT)
    changed["unknown"]["retained"].append("source changed")
    raw_changed = dumps(changed).encode()
    second = import_report(database, fetched(raw_changed))
    third = import_report(database, fetched())

    assert first.created and second.created and not third.created
    assert second.snapshot_id != first.snapshot_id == third.snapshot_id
    assert second.canonical_sha256 != first.canonical_sha256
    rows = stored_rows(database, SourceReportSnapshot)
    assert len(rows) == 2
    assert next(row for row in rows if row["id"] == first.snapshot_id) == initial
    assert next(row for row in rows if row["id"] == second.snapshot_id)["original_content"] == raw_changed
    assert len(stored_rows(database, SourceReportObservation)) == 3


def test_new_source_ids_and_fixture_namespace_cannot_collide_with_api_evidence(database):
    api = import_report(database, fetched())
    fixture = import_report(database, fetched(retrieval_method="fixture"))
    fixture_repeat = import_report(database, fetched(retrieval_method="fixture"))
    another = loads(RAW_REPORT)
    another["report_id"] = 43
    new_id = import_report(database, fetched(dumps(another).encode(), report_id=43))

    assert len({api.snapshot_id, fixture.snapshot_id, new_id.snapshot_id}) == 3
    assert api.created and fixture.created and new_id.created
    assert fixture_repeat.snapshot_id == fixture.snapshot_id and not fixture_repeat.created
    rows = {row["id"]: row for row in stored_rows(database, SourceReportSnapshot)}
    assert rows[api.snapshot_id]["source_system"] == rows[new_id.snapshot_id]["source_system"] == API_SOURCE_SYSTEM
    assert rows[fixture.snapshot_id]["source_system"] == FIXTURE_SOURCE_SYSTEM
    assert rows[new_id.snapshot_id]["source_report_id"] == "43"
    observations = stored_rows(database, SourceReportObservation)
    assert len(observations) == 4
    assert {row["retrieval_method"] for row in observations} == {"api", "fixture"}


@pytest.mark.parametrize("changes", [
    {"fetched_at": FETCHED_AT.replace(tzinfo=None)},
    {"fetched_at": FETCHED_AT.astimezone(timezone(timedelta(hours=-4)))},
    {"fetched_at": "2026-10-31T14:15:16Z"},
    {"source_url": report_url(42) + "?api_key=must-not-be-retained"},
    {"source_url": report_url(43)},
    {"source_url": "https://elsewhere.example/v1/reports/42"},
    {"retrieval_method": "unknown"},
    {"retrieval_method": ["api"]},
    {"request_id": "x" * 201},
    {"request_id": "first\nsecond"},
    {"request_id": "first\rsecond"},
    {"request_id": 123},
])
def test_invalid_provenance_is_rejected_without_any_persistence(database, changes):
    with pytest.raises(ValueError):
        import_report(database, replace(fetched(), **changes))
    assert stored_rows(database, SourceReportSnapshot) == []
    assert stored_rows(database, SourceReportObservation) == []


@pytest.mark.parametrize("raw", [
    b'{"report_id":43,"body":{}}',
    b'{"report_id":42,"body":[]}',
    b'{"report_id":42,"report_id":42,"body":{}}',
    b'not-json-sensitive-marker',
])
def test_persistence_boundary_reparses_identity_and_body(database, raw, caplog):
    with pytest.raises(SourceApiError) as failure:
        import_report(database, fetched(raw))
    assert failure.value.kind == "invalid_response"
    assert raw.decode() not in str(failure.value)
    assert "sensitive-marker" not in caplog.text
    assert stored_rows(database, SourceReportSnapshot) == []
    assert stored_rows(database, SourceReportObservation) == []


def test_observation_failure_rolls_back_the_snapshot_and_successful_retry_creates_it(database):
    def fail_observation(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO SOURCE_REPORT_OBSERVATIONS"):
            raise RuntimeError("synthetic observation failure")

    event.listen(database, "before_cursor_execute", fail_observation)
    try:
        with pytest.raises(RuntimeError, match="synthetic observation failure"):
            import_report(database, fetched())
    finally:
        event.remove(database, "before_cursor_execute", fail_observation)
    assert stored_rows(database, SourceReportSnapshot) == []
    assert stored_rows(database, SourceReportObservation) == []
    receipt = import_report(database, fetched())
    assert receipt.created is True
    assert len(stored_rows(database, SourceReportSnapshot)) == 1
    assert len(stored_rows(database, SourceReportObservation)) == 1


def test_failed_duplicate_observation_preserves_existing_snapshot_and_prior_observations(database):
    import_report(database, fetched())
    before = (
        stored_rows(database, SourceReportSnapshot),
        stored_rows(database, SourceReportObservation),
    )

    def fail_after_observation(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO SOURCE_REPORT_OBSERVATIONS"):
            raise RuntimeError("synthetic post-insert failure")

    event.listen(database, "after_cursor_execute", fail_after_observation)
    try:
        with pytest.raises(RuntimeError, match="synthetic post-insert failure"):
            import_report(database, fetched())
    finally:
        event.remove(database, "after_cursor_execute", fail_after_observation)
    assert stored_rows(database, SourceReportSnapshot) == before[0]
    assert stored_rows(database, SourceReportObservation) == before[1]


def test_commit_failure_returns_no_receipt_and_rolls_back_both_inserted_rows(database):
    def fail_commit(session):
        # Reach the database with both rows before the commit is rejected.
        session.flush()
        raise RuntimeError("synthetic commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            import_report(database, fetched())
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert stored_rows(database, SourceReportSnapshot) == []
    assert stored_rows(database, SourceReportObservation) == []
    assert import_report(database, fetched()).created is True


def test_concurrent_postgres_duplicates_share_one_snapshot_and_commit_both_observations(database):
    if database.dialect.name != "postgresql":
        pytest.skip("The real conflict race requires PostgreSQL transactions")
    both_inserting = Barrier(2, timeout=10)

    def synchronize_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO SOURCE_REPORT_SNAPSHOTS"):
            both_inserting.wait()

    rearranged = dumps(loads(RAW_REPORT), sort_keys=True).encode()
    event.listen(database, "before_cursor_execute", synchronize_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(import_report, database, fetched(raw))
                for raw in (RAW_REPORT, rearranged)
            ]
            receipts = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(database, "before_cursor_execute", synchronize_insert)
    assert sorted(receipt.created for receipt in receipts) == [False, True]
    assert len({receipt.snapshot_id for receipt in receipts}) == 1
    assert len({receipt.observation_id for receipt in receipts}) == 2
    snapshots = stored_rows(database, SourceReportSnapshot)
    observations = stored_rows(database, SourceReportObservation)
    assert len(snapshots) == 1 and len(observations) == 2
    assert snapshots[0]["original_content"] in (RAW_REPORT, rearranged)
    assert {row["raw_sha256"] for row in observations} == {
        sha256(RAW_REPORT).hexdigest(), sha256(rearranged).hexdigest(),
    }


def test_import_does_not_create_its_own_tables(tmp_path):
    with disposable_engine(tmp_path) as empty:
        assert inspect(empty).get_table_names() == []
        with pytest.raises(SQLAlchemyError):
            import_report(empty, fetched())
        assert inspect(empty).get_table_names() == []


def test_private_metadata_and_imports_leave_all_incident_and_review_state_untouched(database):
    # conftest selects a disposable application URL before this import.
    import main

    assert ReportImportBase.metadata is not main.Base.metadata
    assert not set(ReportImportBase.metadata.tables).intersection(main.Base.metadata.tables)
    main.initialize_database(database)
    review = dumps({
        "decision": "NOT_MATERIAL", "reviewer": "synthetic-reviewer",
        "reviewedAt": FETCHED_AT.isoformat(),
    })
    with Session(database) as session:
        session.add(main.IncidentRow(
            id="synthetic-sentinel", title="Synthetic incident",
            log="Synthetic source log", occurred_at=FETCHED_AT,
            created_at=FETCHED_AT, source="local-test", state="NOT_MATERIAL",
            assessment='{"potentiallyMaterial": false}', review=review, related="[]",
        ))
        session.flush()
        session.add(main.ReviewRow(
            id="synthetic-review", incident_id="synthetic-sentinel",
            decision="NOT_MATERIAL", reviewer="synthetic-reviewer",
            reviewed_at=FETCHED_AT,
        ))
        session.commit()

    def app_rows():
        with database.connect() as connection:
            return {
                name: connection.execute(select(table).order_by(*table.primary_key.columns)).mappings().all()
                for name, table in main.Base.metadata.tables.items()
            }

    before = app_rows()
    import_report(database, fetched())
    changed = loads(RAW_REPORT)
    changed["body"]["message"] = "Changed synthetic source"
    import_report(database, fetched(dumps(changed).encode()))
    assert app_rows() == before


def test_original_application_startup_metadata_excludes_private_import_tables(tmp_path):
    import main

    with disposable_engine(tmp_path) as empty:
        main.initialize_database(empty)
        assert set(inspect(empty).get_table_names()) == set(main.Base.metadata.tables)
        assert not set(ReportImportBase.metadata.tables).intersection(inspect(empty).get_table_names())
