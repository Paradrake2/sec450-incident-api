"""Existence checks use normal request DI before decoded-body field validation."""

from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
import pytest

from request_preparation import build_preparation


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)


class LinkBody(BaseModel):
    relatedIncidentId: str = Field(min_length=1)


@dataclass
class Store:
    name: str
    incidents: dict


@dataclass
class SelectedSession:
    store: Store


@contextmanager
def application():
    app = FastAPI()
    normal = Store("normal", {"normal-only": {"id": "normal-only"}})
    alternate = Store("alternate", {"alternate-only": {"id": "alternate-only"}})
    app.state.store = normal
    events = []
    opened = []
    closed = []

    def get_store(request: Request):
        return request.app.state.store

    def get_session(store=Depends(get_store)):
        session = SelectedSession(store)
        opened.append(session)
        try:
            yield session
        finally:
            closed.append(session)

    def lookup(session, incident_id):
        events.append((session.store.name, incident_id, id(session)))
        row = session.store.incidents.get(incident_id)
        if row is None:
            raise HTTPException(
                404, detail={"error": "INCIDENT_NOT_FOUND", "message": "Not found"}
            )
        return row

    prepare_review, prepare_related = build_preparation(
        get_session,
        lookup,
        ReviewBody,
        LinkBody,
    )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            status_code=400,
            content={"error": "INVALID_REQUEST", "message": "Invalid body"},
        )

    @app.middleware("http")
    async def json_media_first(request, call_next):
        if (
            request.method == "POST"
            and request.headers.get("content-type", "").split(";", 1)[0]
            != "application/json"
        ):
            return JSONResponse(
                status_code=415,
                content={"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Use JSON"},
            )
        return await call_next(request)

    @app.post("/incidents/{incidentId}/review")
    def review(prepared=Depends(prepare_review), session=Depends(get_session)):
        row, data = prepared
        assert row is session.store.incidents[row["id"]]
        return {
            "id": row["id"],
            "store": session.store.name,
            "review": data.model_dump(),
        }

    @app.post("/incidents/{incidentId}/related")
    def related(prepared=Depends(prepare_related), session=Depends(get_session)):
        row, data = prepared
        assert row is session.store.incidents[row["id"]]
        return {
            "id": row["id"],
            "store": session.store.name,
            "related": data.model_dump(),
        }

    with TestClient(app) as client:
        yield app, client, get_store, alternate, events, opened, closed


@pytest.mark.parametrize(
    "action,payload",
    [
        ("review", {}),
        ("review", {"decision": None, "reviewer": "synthetic"}),
        ("related", {}),
        ("related", {"relatedIncidentId": None}),
    ],
)
def test_unknown_incident_precedes_decoded_body_field_validation(action, payload):
    with application() as (_, client, _, _, events, opened, closed):
        response = client.post(f"/incidents/missing/{action}", json=payload)
        assert response.status_code == 404
        assert events == [("normal", "missing", id(opened[0]))]
        assert len(opened) == len(closed) == 1


@pytest.mark.parametrize("action", ["review", "related"])
def test_existing_incident_still_rejects_invalid_body_fields(action):
    with application() as (_, client, _, _, events, opened, closed):
        response = client.post(f"/incidents/normal-only/{action}", json={})
        assert response.status_code == 400
        assert len(events) == len(opened) == len(closed) == 1


@pytest.mark.parametrize("action", ["review", "related"])
def test_media_type_is_checked_before_lookup(action):
    with application() as (_, client, _, _, events, opened, closed):
        response = client.post(
            f"/incidents/missing/{action}",
            content="{}",
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert events == opened == closed == []


@pytest.mark.parametrize(
    "action,payload,field",
    [
        ("review", {"decision": "MATERIAL", "reviewer": "synthetic"}, "review"),
        ("related", {"relatedIncidentId": "other"}, "related"),
    ],
)
def test_valid_handler_reuses_one_selected_session_and_lookup(action, payload, field):
    with application() as (_, client, _, _, events, opened, closed):
        response = client.post(f"/incidents/normal-only/{action}", json=payload)
        assert response.status_code == 200
        assert response.json()[field] == payload
        assert len(events) == len(opened) == len(closed) == 1


def test_store_dependency_override_scopes_lookup_and_handler_without_global_access():
    with application() as (app, client, get_store, alternate, events, opened, closed):
        app.dependency_overrides[get_store] = lambda: alternate
        missing = client.post("/incidents/normal-only/review", json={})
        assert missing.status_code == 404
        valid = client.post(
            "/incidents/alternate-only/review",
            json={"decision": "MATERIAL", "reviewer": "synthetic"},
        )
        assert valid.status_code == 200
        assert valid.json()["store"] == "alternate"
        assert [(store, ident) for store, ident, _ in events] == [
            ("alternate", "normal-only"),
            ("alternate", "alternate-only"),
        ]
        assert len(opened) == len(closed) == 2


@pytest.mark.parametrize("action", ["review", "related"])
def test_malformed_json_remains_envelope_error_before_dependency_resolution(action):
    with application() as (_, client, _, _, events, opened, closed):
        response = client.post(
            f"/incidents/missing/{action}",
            content="{",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert events == opened == closed == []


def test_openapi_preserves_body_models_and_one_incident_path_alias():
    with application() as (app, *_):
        schema = app.openapi()
        for action, model in (("review", "ReviewBody"), ("related", "LinkBody")):
            operation = schema["paths"][f"/incidents/{{incidentId}}/{action}"]["post"]
            assert operation["requestBody"]["required"] is True
            assert operation["requestBody"]["content"]["application/json"][
                "schema"
            ] == {"$ref": f"#/components/schemas/{model}"}
            assert [parameter["name"] for parameter in operation["parameters"]] == [
                "incidentId"
            ]
            assert operation["parameters"][0]["in"] == "path"


@pytest.mark.parametrize("bad", [None, str, object])
def test_factory_rejects_non_model_configuration(bad):
    with pytest.raises(TypeError):
        build_preparation(lambda: None, lambda session, ident: {}, bad, LinkBody)
