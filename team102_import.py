"""Operator-only import of one report, optionally creating its incident."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from incident_store import InvalidIncidentError, save_incident
from migration_tools import migration_config
from report_import_store import ImportReceipt, import_report, stage_report
from request_models import CreateIncident
from team102_client import Team102Client
from team102_contract import MAX_REPORT_BYTES, ReportFetch, SourceApiError, parse_report, report_url
from team102_convert import ConversionError, convert_report


def _require_migrated_database(engine):
    expected = ScriptDirectory.from_config(migration_config()).get_current_head()
    with engine.connect() as connection:
        actual = MigrationContext.configure(connection).get_current_revision()
    if actual != expected:
        raise SourceApiError(
            "configuration", "Run python -m migration_tools upgrade for the selected database first"
        )


def _fetch_one(engine, report_id: int, *, client=None, fixture: Path | None = None):
    source_url = report_url(report_id)
    _require_migrated_database(engine)
    if fixture is not None:
        try:
            with Path(fixture).open("rb") as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
        except OSError:
            raise SourceApiError("configuration", "The report fixture could not be read") from None
        fetch = ReportFetch(
            report_id=report_id, raw_content=raw, fetched_at=datetime.now(timezone.utc),
            source_url=source_url, retrieval_method="fixture",
        )
    else:
        if client is None:
            raise SourceApiError("configuration", "A configured Team 102 client is required")
        fetch = client.fetch_report(report_id)
    # Enforce the requested identity even for injected clients.
    if fetch.report_id != report_id:
        raise SourceApiError("invalid_response", "Team 102 returned a different report")
    return fetch


def import_one(engine, report_id: int, *, client=None, fixture: Path | None = None):
    """Preserve one explicit report without creating or changing incidents."""
    return import_report(engine, _fetch_one(engine, report_id, client=client, fixture=fixture))


@dataclass(frozen=True)
class IncidentImportReceipt:
    incident_id: str
    created: bool
    report: ImportReceipt


def import_incident(engine, report_id: int, *, client=None, fixture: Path | None = None):
    """Fetch, validate and convert before atomically retaining evidence/incident.

    Retrying returns the existing incident without replacing its evidence or
    workflow state. Every successful fetch still records a source observation.
    """
    fetch = _fetch_one(engine, report_id, client=client, fixture=fixture)
    document = parse_report(fetch.raw_content, report_id)
    try:
        payload = convert_report(document)
    except (ConversionError, AttributeError, TypeError, ValueError, OverflowError):
        raise SourceApiError(
            "invalid_report", "Team 102 report cannot be converted to an incident"
        ) from None
    if fetch.retrieval_method == "fixture":
        payload["source"] = f"team102-fixture:report:{report_id}"
    data = CreateIncident(**payload)
    with Session(engine) as session, session.begin():
        report = stage_report(session, fetch)
        try:
            row, created = save_incident(session, data)
        except InvalidIncidentError:
            raise SourceApiError(
                "invalid_report", "Team 102 report cannot be converted to an incident"
            ) from None
        receipt = IncidentImportReceipt(incident_id=row.id, created=created, report=report)
    return receipt


def _configured_value(name, file_values):
    return os.environ[name] if name in os.environ else file_values.get(name)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preserve one Team 102 report in the application database")
    parser.add_argument("--report-id", type=int, required=True)
    parser.add_argument("--fixture", type=Path, help="Read a synthetic local fixture instead of the API")
    parser.add_argument("--create-incident", action="store_true", help="Also convert and create the incident atomically")
    args = parser.parse_args(argv)
    engine = None
    try:
        file_values = dotenv_values(Path(__file__).resolve().with_name(".env"))
        raw_url = _configured_value("DATABASE_URL", file_values)
        if not raw_url:
            raise SourceApiError("configuration", "DATABASE_URL must select the application database")
        if raw_url.startswith("postgres://"):
            raw_url = "postgresql://" + raw_url[len("postgres://"):]
        engine = create_engine(raw_url, pool_pre_ping=True)
        importer = import_incident if args.create_incident else import_one
        if args.fixture is not None:
            receipt = importer(engine, args.report_id, fixture=args.fixture)
        else:
            api_key = _configured_value("SEC450_KEY", file_values)
            if not api_key:
                raise SourceApiError("configuration", "SEC450_KEY must be configured locally for API access")
            with Team102Client(api_key) as client:
                receipt = importer(engine, args.report_id, client=client)
        report = receipt.report if args.create_incident else receipt
        output = {
            "status": "imported" if receipt.created else "already_present",
            "reportId": report.source_report_id,
            "snapshotId": report.snapshot_id,
            "observationId": report.observation_id,
            "retrievalMethod": "fixture" if args.fixture is not None else "api",
        }
        if args.create_incident:
            output["incidentId"] = receipt.incident_id
        print(json.dumps(output))
        return 0
    except SourceApiError as exc:
        error = {"error": exc.kind, "message": exc.message}
        if exc.status_code is not None:
            error["upstreamStatus"] = exc.status_code
        if exc.retry_after_seconds is not None:
            error["retryAfterSeconds"] = exc.retry_after_seconds
        print(json.dumps(error), file=sys.stderr)
        return 1
    except (SQLAlchemyError, CommandError):
        print(json.dumps({
            "error": "storage", "message": "Report import failed; check database access and migrations",
        }), file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print(json.dumps({
            "error": "configuration", "message": "Report import configuration or input is invalid",
        }), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
