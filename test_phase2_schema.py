"""The historical contract remains independent of application model changes."""

import json

import httpx
from jsonschema import Draft202012Validator, FormatChecker
import pytest

from phase2_schema import load_contract, operation_inventory, validate_response


INCIDENT_PATH = "/api/v1/incidents/{incidentId}"


def incident(**changes):
    value = {
        "id": "synthetic-id",
        "title": "PRIVATE TITLE",
        "log": "PRIVATE EVIDENCE",
        "occurredAt": "2026-10-31T12:00:00Z",
        "createdAt": "2026-10-31T12:01:00+00:00",
        "state": "RECEIVED",
        "relatedIncidentIds": [],
    }
    return {**value, **changes}


def response(status, document=None, *, raw=None, content_type="application/json"):
    if raw is None:
        raw = json.dumps(document).encode()
    headers = {} if content_type is None else {"Content-Type": content_type}
    return httpx.Response(status, content=raw, headers=headers)


def check_incident(document):
    return validate_response(
        load_contract(), "GET", INCIDENT_PATH, response(200, document), 200
    )


def input_validator(contract, schema):
    return Draft202012Validator(
        {**contract, "allOf": [schema]}, format_checker=FormatChecker()
    )


def test_versioned_baseline_and_inventory_cover_every_common_operation():
    contract = load_contract()
    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == "1.0.0"
    assert contract["servers"][0]["url"] == "https://{team-base-url}/api/v1"
    inventory = operation_inventory(contract)
    assert len(inventory) == 9
    assert {(item["method"], item["path"]) for item in inventory} == {
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/test/reset"),
        ("POST", "/api/v1/incidents"),
        ("GET", "/api/v1/incidents"),
        ("GET", INCIDENT_PATH),
        ("POST", INCIDENT_PATH + "/assessment"),
        ("POST", INCIDENT_PATH + "/review"),
        ("POST", INCIDENT_PATH + "/related"),
        ("POST", INCIDENT_PATH + "/disclosure"),
    }
    by_operation = {(item["method"], item["path"]): item for item in inventory}
    create = by_operation[("POST", "/api/v1/incidents")]
    assert create["requirements"] == ["REQ-01", "REQ-04", "REQ-08"]
    assert create["successStatuses"] == [201]
    assert create["errorStatuses"] == [400, 415, 500]
    assert by_operation[("GET", "/api/v1/health")]["errorStatuses"] == [503]
    assert (
        "restores the shared test fixture state"
        in contract["paths"]["/test/reset"]["post"]["description"]
    )


def test_loading_and_inventory_do_not_share_mutable_caller_state():
    contract = load_contract()
    inventory = operation_inventory(contract)
    inventory[1]["requirements"].append("CALLER-CHANGE")
    assert (
        "CALLER-CHANGE"
        not in contract["paths"]["/test/reset"]["post"]["x-requirements"]
    )
    contract["info"]["version"] = "caller-change"
    assert load_contract()["info"]["version"] == "1.0.0"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"source": None, "assessment": None, "review": None},
        {"PRIVATE UNKNOWN KEY": "PRIVATE VALUE"},
        {
            "assessment": {
                "potentiallyMaterial": False,
                "explanation": "Synthetic explanation",
            }
        },
        {
            "review": {
                "decision": "MATERIAL",
                "reviewer": "Synthetic reviewer",
                "rationale": None,
                "reviewedAt": "2026-10-31T12:02:00Z",
            }
        },
    ],
)
def test_incident_schema_internal_refs_and_nullable_responses_are_preserved(changes):
    assert check_incident(incident(**changes)) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"id": 123},
        {"title": None},
        {"state": "PRIVATE INVALID STATE"},
        {"relatedIncidentIds": [123]},
        {"occurredAt": "not-a-date"},
        {"createdAt": "2026-10-31T12:01:00"},
        {"occurredAt": "2026-02-30T12:00:00Z"},
        {"assessment": {"potentiallyMaterial": "true", "explanation": "PRIVATE"}},
        {"assessment": {"potentiallyMaterial": 1, "explanation": "PRIVATE"}},
        {"assessment": {"potentiallyMaterial": True, "explanation": ""}},
        {
            "review": {
                "decision": "MATERIAL",
                "reviewer": "PRIVATE",
                "reviewedAt": "PRIVATE INVALID DATE",
            }
        },
    ],
)
def test_response_types_dates_enums_and_nested_refs_are_checked_without_coercion(
    changes,
):
    problems = check_incident(incident(**changes))
    assert problems == ["Response body does not conform to the declared schema"]
    assert "PRIVATE" not in json.dumps(problems)


def test_missing_required_response_fields_are_not_filled_from_model_defaults():
    document = incident()
    del document["relatedIncidentIds"]
    assert check_incident(document)


def test_list_schema_validates_each_incident_item():
    contract = load_contract()
    assert (
        validate_response(
            contract, "GET", "/api/v1/incidents", response(200, [incident()]), 200
        )
        == []
    )
    assert validate_response(
        contract, "GET", "/api/v1/incidents", response(200, [incident(id=2)]), 200
    )


@pytest.mark.parametrize("status", ["ok", "PRIVATE WRONG VALUE", True])
def test_health_const_is_validated(status):
    problems = validate_response(
        load_contract(), "GET", "/api/v1/health", response(200, {"status": status}), 200
    )
    assert bool(problems) is (status != "ok")
    assert "PRIVATE" not in json.dumps(problems)


@pytest.mark.parametrize("final", [False, True, 0, "false"])
def test_disclosure_final_is_boolean_false_not_a_truthy_coercion(final):
    document = {
        "incidentId": "synthetic",
        "draft": "",
        "generatedAt": "2026-10-31T12:00:00Z",
        "final": final,
    }
    problems = validate_response(
        load_contract(),
        "POST",
        INCIDENT_PATH + "/disclosure",
        response(200, document),
        200,
    )
    # The written schema allows an empty draft; AC-07 separately forbids it.
    assert bool(problems) is (final is not False)


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("POST", "/api/v1/incidents", 400),
        ("GET", INCIDENT_PATH, 404),
        ("POST", INCIDENT_PATH + "/assessment", 409),
        ("POST", "/api/v1/test/reset", 500),
    ],
)
def test_error_response_component_and_schema_refs_are_resolved(method, path, status):
    contract = load_contract()
    assert (
        validate_response(
            contract,
            method,
            path,
            response(
                status,
                {"error": "synthetic", "message": "PRIVATE MESSAGE", "field": None},
            ),
            status,
        )
        == []
    )
    problems = validate_response(
        contract,
        method,
        path,
        response(status, {"error": 1, "message": "PRIVATE MESSAGE"}),
        status,
    )
    assert problems == ["Response body does not conform to the declared schema"]
    assert "PRIVATE" not in json.dumps(problems)


def test_reset_204_has_no_body_even_if_it_is_only_whitespace():
    contract = load_contract()
    assert (
        validate_response(
            contract,
            "POST",
            "/api/v1/test/reset",
            response(204, raw=b"", content_type=None),
            204,
        )
        == []
    )
    assert validate_response(
        contract, "POST", "/api/v1/test/reset", response(204, raw=b" "), 204
    ) == ["No-content response must have an empty body"]


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("POST", "/api/v1/incidents", 415),
        ("GET", "/api/v1/health", 503),
    ],
)
def test_untyped_errors_do_not_acquire_an_invented_body_schema(method, path, status):
    assert (
        validate_response(
            load_contract(),
            method,
            path,
            response(status, raw=b"PRIVATE BODY", content_type=None),
            status,
        )
        == []
    )


@pytest.mark.parametrize(
    "content_type", ["application/json", "Application/JSON; charset=utf-8"]
)
def test_json_media_type_parameters_and_case_are_supported(content_type):
    assert (
        validate_response(
            load_contract(),
            "GET",
            INCIDENT_PATH,
            response(200, incident(), content_type=content_type),
            200,
        )
        == []
    )


@pytest.mark.parametrize(
    "content_type", [None, "text/plain", "application/problem+json"]
)
def test_typed_responses_require_the_declared_json_media_type(content_type):
    assert validate_response(
        load_contract(),
        "GET",
        INCIDENT_PATH,
        response(200, incident(), content_type=content_type),
        200,
    ) == ["JSON response Content-Type is required"]


@pytest.mark.parametrize(
    "raw",
    [b"PRIVATE INVALID JSON", b"\xff", b'{"status":"ok","PRIVATE":NaN}', b'"\\ud800"'],
)
def test_invalid_json_diagnostics_never_include_response_contents(raw):
    problems = validate_response(
        load_contract(), "GET", "/api/v1/health", response(200, raw=raw), 200
    )
    assert problems == ["Response body is not valid JSON"]
    assert "PRIVATE" not in json.dumps(problems)


def test_wrong_status_unknown_operation_and_undeclared_expectation_are_distinct():
    contract = load_contract()
    assert validate_response(
        contract,
        "POST",
        "/api/v1/incidents",
        response(400, {"PRIVATE": "PRIVATE"}),
        201,
    ) == ["Response status does not match expected status"]
    assert validate_response(
        contract, "POST", "/api/v1/incidents", response(418), 418
    ) == ["Expected status is not declared for this operation"]
    assert validate_response(contract, "GET", "/PRIVATE", response(200), 200) == [
        "Unknown contract operation"
    ]


@pytest.mark.parametrize("where", ["schema", "response", "unused"])
def test_remote_refs_are_rejected_before_any_resolver_can_fetch(monkeypatch, where):
    import phase2_schema

    contract = load_contract()
    reference = {"$ref": "https://PRIVATE.example/SECRET"}
    if where == "schema":
        contract["components"]["schemas"]["Incident"] = reference
    elif where == "response":
        contract["paths"]["/incidents/{incidentId}"]["get"]["responses"]["200"] = (
            reference
        )
    else:
        contract["components"]["schemas"]["Unused"] = reference
    monkeypatch.setattr(
        phase2_schema,
        "_no_remote_resource",
        lambda uri: pytest.fail("No remote fetch permitted"),
    )
    problems = validate_response(
        contract, "GET", INCIDENT_PATH, response(200, incident()), 200
    )
    assert problems == ["Contract references must be internal JSON pointers"]
    assert "PRIVATE" not in json.dumps(problems) and "SECRET" not in json.dumps(
        problems
    )


def test_missing_or_cyclic_internal_refs_are_sanitized():
    contract = load_contract()
    contract["paths"]["/incidents/{incidentId}"]["get"]["responses"]["200"] = {
        "$ref": "#/components/responses/PRIVATE"
    }
    assert validate_response(
        contract, "GET", INCIDENT_PATH, response(200, incident()), 200
    ) == ["Contract response definition is invalid"]
    contract = load_contract()
    contract["components"]["schemas"]["Incident"] = {
        "$ref": "#/components/schemas/PRIVATE"
    }
    assert validate_response(
        contract, "GET", INCIDENT_PATH, response(200, incident()), 200
    ) == ["Contract schema validation could not be completed"]
    contract = load_contract()
    contract["components"]["responses"]["InternalError"] = {
        "$ref": "#/components/responses/InternalError"
    }
    assert validate_response(
        contract, "POST", "/api/v1/test/reset", response(500, {}), 500
    ) == ["Contract response definition is invalid"]


def test_request_optional_strings_do_not_accept_null_and_closed_requests_reject_extras():
    contract = load_contract()
    schemas = contract["components"]["schemas"]
    create = input_validator(contract, schemas["CreateIncidentRequest"])
    create_input = {
        "title": "Synthetic",
        "log": "Synthetic",
        "occurredAt": "2026-10-31T12:00:00Z",
    }
    assert create.is_valid(create_input)
    assert not create.is_valid({**create_input, "source": None})
    assert not create.is_valid({**create_input, "extra": True})
    review = input_validator(contract, schemas["ReviewRequest"])
    review_input = {"decision": "MATERIAL", "reviewer": "Synthetic reviewer"}
    assert review.is_valid(review_input)
    assert not review.is_valid({**review_input, "rationale": None})
    assert not review.is_valid({**review_input, "extra": True})


def test_related_request_remains_open_to_unknown_properties_in_original_schema():
    contract = load_contract()
    related = contract["paths"]["/incidents/{incidentId}/related"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    validator = input_validator(contract, related)
    assert "additionalProperties" not in related
    assert validator.is_valid({"relatedIncidentId": "synthetic", "unknown": True})
    assert not validator.is_valid({"relatedIncidentId": ""})
