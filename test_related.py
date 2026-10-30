"""REQ-06 / AC-06: durable, symmetric incident relationships and failure paths."""

from json import dumps

from sqlalchemy import select
from sqlalchemy.orm import Session

from test_api import TestClient, app, engine, new_incident
from main import IncidentRow, RelationRow, backfill_legacy_relations


def make(client, log="Routine synthetic incident log"):
    response = new_incident(client, log)
    assert response.status_code == 201
    return response.json()["id"]


def link(client, source, target):
    return client.post(
        f"/api/v1/incidents/{source}/related",
        json={"relatedIncidentId": target},
    )


def fetch(client, ident):
    response = client.get(f"/api/v1/incidents/{ident}")
    assert response.status_code == 200
    return response.json()


def count_links():
    with Session(engine) as session:
        return len(session.scalars(select(RelationRow)).all())


def test_ac06_links_are_symmetric_and_returned_by_create_list_get():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        response = link(c, a, b)
        assert response.status_code == 200
        assert response.json()["relatedIncidentIds"] == [b]
        assert fetch(c, a)["relatedIncidentIds"] == [b]
        assert fetch(c, b)["relatedIncidentIds"] == [a]
        entries = {row["id"]: row for row in c.get("/api/v1/incidents").json()}
        assert entries[a]["relatedIncidentIds"] == [b]
        assert entries[b]["relatedIncidentIds"] == [a]
        assert count_links() == 1


def test_ac06_linking_cannot_change_assessment_or_human_materiality_state():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = make(c, "Possible ransomware on a production server")
        b = make(c)
        assert c.post(f"/api/v1/incidents/{a}/assessment").status_code == 200
        assert c.post(
            f"/api/v1/incidents/{a}/review",
            json={"decision": "MATERIAL", "reviewer": "test-legal"},
        ).status_code == 200
        before_a, before_b = fetch(c, a), fetch(c, b)
        assert link(c, a, b).status_code == 200
        after_a, after_b = fetch(c, a), fetch(c, b)
        for name in ("state", "assessment", "review", "log", "occurredAt"):
            assert after_a[name] == before_a[name]
            assert after_b[name] == before_b[name]
        assert after_a["relatedIncidentIds"] == [b]
        assert after_b["relatedIncidentIds"] == [a]


def test_ac06_duplicate_and_reverse_duplicate_return_409_no_mutation():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        assert link(c, a, b).status_code == 200
        before_a, before_b = fetch(c, a), fetch(c, b)
        for source, target in ((a, b), (b, a)):
            error = link(c, source, target)
            assert error.status_code == 409
            assert error.json()["error"] == "DUPLICATE_RELATION"
            assert error.json()["message"]
            assert fetch(c, a) == before_a
            assert fetch(c, b) == before_b
        assert count_links() == 1


def test_ac06_self_relation_returns_409_and_does_not_write():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = make(c)
        before = fetch(c, a)
        response = link(c, a, a)
        assert response.status_code == 409
        assert response.json()["error"] == "DUPLICATE_RELATION"
        assert fetch(c, a) == before
        assert count_links() == 0


def test_ac06_missing_source_or_target_returns_404_without_mutation():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = make(c)
        before = fetch(c, a)
        for source, target in ((a, "unknown"), ("unknown", a)):
            response = link(c, source, target)
            assert response.status_code == 404
            assert response.json()["error"] == "INCIDENT_NOT_FOUND"
            assert fetch(c, a) == before
        assert count_links() == 0


def test_ac06_missing_and_empty_related_id_return_400():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = make(c)
        for body in ({}, {"relatedIncidentId": ""}, {"relatedIncidentId": 23}):
            response = c.post(f"/api/v1/incidents/{a}/related", json=body)
            assert response.status_code == 400
            assert response.json()["error"] == "INVALID_REQUEST"
        assert fetch(c, a)["relatedIncidentIds"] == []


def test_ac06_closed_source_and_target_cannot_be_linked():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        with Session(engine) as session:
            session.get(IncidentRow, a).state = "CLOSED"
            session.commit()
        assert link(c, a, b).status_code == 409
        assert link(c, b, a).status_code == 409
        assert count_links() == 0
        assert fetch(c, a)["relatedIncidentIds"] == []
        assert fetch(c, b)["relatedIncidentIds"] == []


def test_ac06_supports_multiple_links_without_duplicates():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b, d = make(c), make(c), make(c)
        assert link(c, a, b).status_code == 200
        assert link(c, d, a).status_code == 200
        assert fetch(c, a)["relatedIncidentIds"] == sorted([b, d])
        assert fetch(c, b)["relatedIncidentIds"] == [a]
        assert fetch(c, d)["relatedIncidentIds"] == [a]
        assert count_links() == 2


def test_ac06_linked_incidents_survive_app_lifespan_restart():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        assert link(c, a, b).status_code == 200
    with TestClient(app) as c:
        assert fetch(c, a)["relatedIncidentIds"] == [b]
        assert fetch(c, b)["relatedIncidentIds"] == [a]
        assert count_links() == 1


def test_ac06_backfills_symmetric_legacy_json_once_and_is_repeatable():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        with Session(engine) as session:
            session.get(IncidentRow, a).related = dumps([b])
            session.get(IncidentRow, b).related = dumps([a])
            session.commit()
        backfill_legacy_relations()
        backfill_legacy_relations()
        assert count_links() == 1
        assert fetch(c, a)["relatedIncidentIds"] == [b]
        assert fetch(c, b)["relatedIncidentIds"] == [a]
    with TestClient(app) as c:
        assert count_links() == 1
        assert fetch(c, a)["relatedIncidentIds"] == [b]


def test_ac06_backfills_one_way_legacy_json_as_symmetric_link():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        with Session(engine) as session:
            session.get(IncidentRow, a).related = dumps([b])
            session.commit()
        backfill_legacy_relations()
        assert fetch(c, a)["relatedIncidentIds"] == [b]
        assert fetch(c, b)["relatedIncidentIds"] == [a]


def test_ac06_backfill_skips_invalid_or_dangling_legacy_ids():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        with Session(engine) as session:
            session.get(IncidentRow, a).related = dumps([b, b, a, "nonexistent", None])
            session.commit()
        backfill_legacy_relations()
        assert count_links() == 1
        assert fetch(c, a)["relatedIncidentIds"] == [b]
        assert fetch(c, b)["relatedIncidentIds"] == [a]


def test_ac06_reset_removes_relations_and_prevents_resurrection():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a, b = make(c), make(c)
        assert link(c, a, b).status_code == 200
        assert count_links() == 1
        assert c.post("/api/v1/test/reset").status_code == 204
        assert count_links() == 0
        assert c.get(f"/api/v1/incidents/{a}").status_code == 404
    with TestClient(app) as c:
        assert count_links() == 0
