"""Manual import entry point checks without accessing a real source."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from migration_tools import upgrade_database
from report_import_store import SourceReportObservation, SourceReportSnapshot
from sqlalchemy.orm import Session
from team102_client import Team102Client
from team102_contract import SourceApiError
from team102_import import import_one
from testing_database import disposable_engine


FIXTURE = Path(__file__).resolve().parent / "examples" / "team102-report.synthetic.json"


def test_http_fetch_to_migrated_store_preserves_report_and_deduplicates(tmp_path):
    calls = []
    raw = b'{"report_id":17,"body":{"summary":"Synthetic upstream report"}}'

    def source(request):
        calls.append(request)
        return httpx.Response(200, content=raw, headers={
            "Content-Type": "application/json", "X-Request-ID": "synthetic-1",
        })

    with disposable_engine(tmp_path) as engine:
        upgrade_database(engine)
        with Team102Client("synthetic-key", transport=httpx.MockTransport(source)) as client:
            first = import_one(engine, 17, client=client)
            second = import_one(engine, 17, client=client)
        assert first.created and not second.created
        assert first.snapshot_id == second.snapshot_id
        assert first.observation_id != second.observation_id
        assert len(calls) == 2
        assert calls[0].method == "GET"
        with Session(engine) as session:
            snapshots = session.scalars(select(SourceReportSnapshot)).all()
            observations = session.scalars(select(SourceReportObservation)).all()
            assert len(snapshots) == 1 and len(observations) == 2
            assert snapshots[0].original_content == raw
            assert snapshots[0].source_system == "team102-query-api"
            assert all(row.retrieval_method == "api" for row in observations)


def test_fixture_import_is_explicit_and_uses_a_separate_source_namespace(tmp_path):
    with disposable_engine(tmp_path) as engine:
        upgrade_database(engine)
        receipt = import_one(engine, 17, fixture=FIXTURE)
        with Session(engine) as session:
            snapshot = session.get(SourceReportSnapshot, receipt.snapshot_id)
            observation = session.get(SourceReportObservation, receipt.observation_id)
            assert snapshot.source_system == "team102-query-api-fixture"
            assert observation.retrieval_method == "fixture"


def test_missing_migration_is_rejected_before_any_source_request(tmp_path):
    def forbidden(request):
        pytest.fail("No source call should occur before the destination is ready")

    with disposable_engine(tmp_path) as engine:
        with Team102Client("synthetic-key", transport=httpx.MockTransport(forbidden)) as client:
            with pytest.raises(SourceApiError) as failure:
                import_one(engine, 17, client=client)
        assert failure.value.kind == "configuration"
        assert "migration_tools" in failure.value.message


def test_operator_cli_errors_do_not_echo_keys_or_database_credentials(monkeypatch, capsys):
    import team102_import

    monkeypatch.setattr(team102_import, "dotenv_values", lambda path: {})
    monkeypatch.setenv("DATABASE_URL", "not-a-url private-database-password")
    monkeypatch.setenv("SEC450_KEY", "private-api-token")
    assert team102_import.main(["--report-id", "17"]) == 1
    captured = capsys.readouterr()
    assert "private" not in captured.err
    assert captured.out == ""


def test_operator_cli_sanitizes_ambiguous_migration_history(tmp_path, monkeypatch, capsys):
    import team102_import

    with disposable_engine(tmp_path) as engine:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            connection.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES ('0001_baseline'), ('0002_report_imports')"
            ))
        monkeypatch.setattr(team102_import, "dotenv_values", lambda path: {})
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        monkeypatch.setattr(team102_import, "create_engine", lambda *args, **kwargs: engine)
        assert team102_import.main(["--report-id", "17", "--fixture", str(FIXTURE)]) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert '"error": "storage"' in captured.err
        assert "Traceback" not in captured.err
