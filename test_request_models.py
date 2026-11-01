"""Request validation is checked against the independent Phase 2 schemas."""

from datetime import datetime, timedelta, timezone

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
import pytest

from phase2_schema import load_contract
from request_models import CreateIncident, LinkRequest, ReviewRequest


CREATE = {
    "title": "Synthetic incident",
    "log": "Synthetic security log",
    "occurredAt": "2026-10-31T12:00:00Z",
}
REVIEW = {"decision": "MATERIAL", "reviewer": "Synthetic reviewer"}


def request_validator(name):
    contract = load_contract()
    if name == "LinkRequest":
        schema = contract["paths"]["/incidents/{incidentId}/related"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
    else:
        schema = contract["components"]["schemas"][name]
    return Draft202012Validator(
        {**contract, "allOf": [schema]}, format_checker=FormatChecker()
    )


@pytest.mark.parametrize(
    "model,baseline,base,field",
    [
        (CreateIncident, "CreateIncidentRequest", CREATE, "source"),
        (ReviewRequest, "ReviewRequest", REVIEW, "rationale"),
    ],
)
def test_optional_field_omission_and_explicit_null_have_different_meanings(
    model, baseline, base, field
):
    validator = request_validator(baseline)
    assert validator.is_valid(base)
    instance = model.model_validate(base)
    assert getattr(instance, field) is None
    assert field not in instance.model_fields_set
    assert not validator.is_valid({**base, field: None})
    with pytest.raises(ValidationError) as failure:
        model.model_validate({**base, field: None})
    assert failure.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize(
    "model,base,field",
    [
        (CreateIncident, CREATE, "source"),
        (ReviewRequest, REVIEW, "rationale"),
    ],
)
def test_generated_optional_field_schema_is_nonnullable_without_null_default(
    model, base, field
):
    schema = model.model_json_schema()
    property_schema = schema["properties"][field]
    assert field not in schema["required"]
    assert property_schema["type"] == "string"
    assert "anyOf" not in property_schema
    assert "default" not in property_schema
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert validator.is_valid(base)
    assert not validator.is_valid({**base, field: None})


@pytest.mark.parametrize(
    "timestamp,offset",
    [
        ("2026-10-31T12:00:00Z", timedelta(0)),
        ("2026-10-31t12:00:00z", timedelta(0)),
        ("2026-10-31T12:00:00.123456Z", timedelta(0)),
        ("2026-10-31T12:00:00+05:30", timedelta(hours=5, minutes=30)),
        ("2026-10-31T12:00:00-04:00", -timedelta(hours=4)),
        ("2026-10-31T12:00:00-00:00", timedelta(0)),
    ],
)
def test_rfc3339_timezone_strings_match_the_baseline_and_preserve_offset(
    timestamp, offset
):
    payload = {**CREATE, "occurredAt": timestamp}
    assert request_validator("CreateIncidentRequest").is_valid(payload)
    model = CreateIncident.model_validate(payload)
    assert model.occurredAt.utcoffset() == offset


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        True,
        1793448000,
        1793448000.5,
        "1793448000",
        "2026-10-31",
        "2026-10-31T12:00:00",
        "2026-10-31 12:00:00Z",
        "2026-10-31T12:00Z",
        "2026-10-31T12:00:00+0000",
        "2026-10-31T12:00:00,5Z",
        " 2026-10-31T12:00:00Z",
        "2026-02-30T12:00:00Z",
        "2026-10-31T25:00:00Z",
        "2026-10-31T12:00:00+24:00",
        "not-a-timestamp",
    ],
)
def test_noncontract_timestamp_inputs_are_rejected_by_both_validators(timestamp):
    payload = {**CREATE, "occurredAt": timestamp}
    assert not request_validator("CreateIncidentRequest").is_valid(payload)
    with pytest.raises(ValidationError):
        CreateIncident.model_validate(payload)


def test_python_datetime_is_not_a_json_string_input():
    with pytest.raises(ValidationError):
        CreateIncident.model_validate(
            {**CREATE, "occurredAt": datetime(2026, 10, 31, tzinfo=timezone.utc)}
        )


def test_timestamp_must_fill_the_entire_string_without_a_trailing_newline():
    # Some date-time format-checker versions use a permissive end anchor.
    # The request parser must still enforce the complete RFC wire string.
    with pytest.raises(ValidationError):
        CreateIncident.model_validate(
            {**CREATE, "occurredAt": "2026-10-31T12:00:00Z\n"}
        )


@pytest.mark.parametrize(
    "model,baseline,payload",
    [
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "source": "sensor"}),
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "source": ""}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "rationale": "Synthetic"}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "rationale": ""}),
    ],
)
def test_explicit_optional_strings_remain_valid(model, baseline, payload):
    assert request_validator(baseline).is_valid(payload)
    instance = model.model_validate(payload)
    assert instance.model_dump(exclude_unset=True) == {
        **payload,
        **({"occurredAt": instance.occurredAt} if model is CreateIncident else {}),
    }


@pytest.mark.parametrize(
    "model,baseline,payload",
    [
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "extra": "ignored?"}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "extra": "ignored?"}),
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "source": 17}),
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "source": "x" * 101}),
        (CreateIncident, "CreateIncidentRequest", {**CREATE, "title": "x" * 201}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "rationale": 17}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "decision": "UNKNOWN"}),
        (ReviewRequest, "ReviewRequest", {**REVIEW, "reviewer": ""}),
    ],
)
def test_other_request_constraints_remain_enforced(model, baseline, payload):
    assert not request_validator(baseline).is_valid(payload)
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_related_request_accepts_extensions_without_persisting_or_redefining_them():
    payload = {
        "relatedIncidentId": "synthetic-id",
        "annotation": {"source": "Synthetic extension"},
    }
    assert request_validator("LinkRequest").is_valid(payload)
    instance = LinkRequest.model_validate(payload)
    assert instance.relatedIncidentId == "synthetic-id"
    assert instance.model_dump() == {"relatedIncidentId": "synthetic-id"}
    schema = LinkRequest.model_json_schema()
    assert schema.get("additionalProperties") is not False
    assert Draft202012Validator(schema).is_valid(payload)


@pytest.mark.parametrize("identifier", [None, 17, ""])
def test_related_request_still_requires_a_nonempty_string_identifier(identifier):
    payload = {"relatedIncidentId": identifier, "extension": "allowed"}
    assert not request_validator("LinkRequest").is_valid(payload)
    with pytest.raises(ValidationError):
        LinkRequest.model_validate(payload)
