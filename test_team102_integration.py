"""Phase 5 integration tests: Team 102 report -> converter -> our incident API.

Uses synthetic reports only (no real Team 102 data) and the same disposable
test database as the rest of the suite (see conftest.py).
"""

import pytest

from test_api import TestClient, app  # sets up the temp DB first
from team102_convert import ConversionError, convert_report


# fake reports shaped like Team 102's /v1/reports format
RANSOMWARE_REPORT = {
    "report_id": 501,
    "body": {
        "summary": "Possible ransomware on file server fs01",
        "matched_rule": {"name": "ransom-note-detected"},
        "timeline": [
            {"ts": "2026-11-10T14:05:00Z"},
            {"ts": "2026-11-10T14:00:00Z"},
        ],
        "raw_lines": [
            "fs01 ransomware note README_RESTORE.txt created",
            "fs01 mass file rename to .locked",
        ],
        "ai_assessment": {},
    },
}

ROUTINE_REPORT = {
    "report_id": 502,
    "body": {
        "summary": "Scheduled backup finished",
        "matched_rule": {},
        "timeline": [{"ts": "2026-11-10T02:00:00Z"}],
        "raw_lines": ["backup job nightly-01 completed ok"],
        "ai_assessment": {},
    },
}


def create_from_report(client, report):
    payload = convert_report(report)
    return client.post("/api/v1/incidents", json=payload)


def count_incidents(client):
    return len(client.get("/api/v1/incidents").json())


def test_converted_report_creates_incident_and_reads_back():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        res = create_from_report(client, RANSOMWARE_REPORT)
        assert res.status_code == 201
        incident = res.json()
        assert incident["state"] == "RECEIVED"
        assert incident["source"] == "team102:report:501"
        assert incident["title"] == "Possible ransomware on file server fs01"

        got = client.get(f"/api/v1/incidents/{incident['id']}")
        assert got.status_code == 200
        assert got.json()["source"] == "team102:report:501"
        assert "ransomware note" in got.json()["log"]
        # converter should pick the earliest timeline time
        assert got.json()["occurredAt"].startswith("2026-11-10T14:00:00")


def test_converted_report_goes_through_full_workflow():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_from_report(client, RANSOMWARE_REPORT).json()["id"]

        assess = client.post(f"/api/v1/incidents/{ident}/assessment")
        assert assess.status_code == 200
        assert assess.json()["potentiallyMaterial"] is True
        assert client.get(f"/api/v1/incidents/{ident}").json()["state"] == "POTENTIALLY_MATERIAL"

        review = client.post(f"/api/v1/incidents/{ident}/review",
                             json={"decision": "MATERIAL", "reviewer": "legal-1"})
        assert review.status_code == 200
        assert review.json()["state"] == "MATERIAL_CONFIRMED"

        draft = client.post(f"/api/v1/incidents/{ident}/disclosure")
        assert draft.status_code == 200
        assert draft.json()["final"] is False


def test_routine_report_is_not_flagged():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_from_report(client, ROUTINE_REPORT).json()["id"]
        assess = client.post(f"/api/v1/incidents/{ident}/assessment")
        assert assess.status_code == 200
        assert assess.json()["potentiallyMaterial"] is False
        assert client.get(f"/api/v1/incidents/{ident}").json()["state"] == "UNDER_REVIEW"


@pytest.mark.parametrize("bad_report", [
    {},                                                         # not a report at all
    {"report_id": "abc", "body": {}},                           # id isnt an int
    {"report_id": 503, "body": {"summary": "no time here"}},    # no timeline time
    {"report_id": 504, "body": {"timeline": [{"ts": "2026-11-10T00:00:00Z"}]}},  # no text
    {"report_id": 505, "body": {"summary": "bad time", "timeline": [{"ts": "not-a-date"}]}},
])
def test_bad_reports_are_rejected_and_nothing_is_created(bad_report):
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        before = count_incidents(client)
        with pytest.raises(ConversionError):
            create_from_report(client, bad_report)
        assert count_incidents(client) == before


def test_converted_incident_survives_restart():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_from_report(client, RANSOMWARE_REPORT).json()["id"]
        client.post(f"/api/v1/incidents/{ident}/assessment")
    # new client = app starts up again against the same database
    with TestClient(app) as client:
        got = client.get(f"/api/v1/incidents/{ident}")
        assert got.status_code == 200
        assert got.json()["source"] == "team102:report:501"
        assert got.json()["state"] == "POTENTIALLY_MATERIAL"


def test_same_report_twice_only_makes_one_incident():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        create_from_report(client, RANSOMWARE_REPORT)
        create_from_report(client, RANSOMWARE_REPORT)
        sources = [i["source"] for i in client.get("/api/v1/incidents").json()]
        assert sources.count("team102:report:501") == 1
