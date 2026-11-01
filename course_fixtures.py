"""Versioned Team 105 candidate fixtures; no claim of pod label agreement."""

import json
from pathlib import Path
from uuid import UUID

from request_models import CreateIncident


FIXTURE_PATH = Path(__file__).resolve().with_name("fixtures") / "incident-v1.json"


def load_fixture_profile():
    profile = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    if profile.get("profile_id") != "team105-incident-v1" or profile.get("agreement_status") != "candidate_not_pod_approved":
        raise ValueError("Unknown course fixture profile")
    incidents = profile.get("incidents")
    if not isinstance(incidents, list) or not incidents:
        raise ValueError("Course fixtures must contain synthetic incidents")
    ids = set()
    for row in incidents:
        ident = row["id"]
        if str(UUID(ident)) != ident or ident in ids or type(row.get("expectedPotentiallyMaterial")) is not bool:
            raise ValueError("Invalid course fixture identity or label")
        ids.add(ident)
        CreateIncident.model_validate({name: row[name] for name in ("title", "log", "occurredAt", "source")})
    return profile


def make_fixture_seed(profile=None):
    profile = load_fixture_profile() if profile is None else profile
    rows = []
    for row in profile["incidents"]:
        data = CreateIncident.model_validate({name: row[name] for name in ("title", "log", "occurredAt", "source")})
        rows.append((row["id"], data))

    def seed(session):
        # Import models only after the selected application/test store exists.
        from main import IncidentRow
        for ident, data in rows:
            session.add(IncidentRow(
                id=ident, title=data.title, log=data.log, source=data.source,
                occurred_at=data.occurredAt, created_at=data.occurredAt,
                state="RECEIVED", related="[]", assessment=None, review=None,
            ))

    return seed
