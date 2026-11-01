"""Request schemas from the recovered Phase 2 incident API contract.

Optional source/rationale fields may be omitted, but explicit JSON null is
invalid. Their corresponding response fields remain nullable in the separate
contract_schemas module.
"""

import re
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema


_RFC3339 = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII | re.IGNORECASE,
)


def _timestamp_string(value):
    # AwareDatetime validates calendar values/offsets after this wire-format
    # check. Pydantic's broader datetime parser also accepts numeric epochs,
    # dates, and several non-RFC spellings, which the common API does not.
    if not isinstance(value, str) or _RFC3339.fullmatch(value) is None:
        raise ValueError("occurredAt must be an RFC 3339 string with a timezone")
    return value


def _remove_default(schema):
    # None is the internal omission sentinel, not an advertised JSON value.
    schema.pop("default", None)


def _reject_explicit_null(value):
    if value is None:
        raise ValueError("This field may be omitted but must not be null")
    return value


TimestampString = Annotated[AwareDatetime, BeforeValidator(_timestamp_string)]


class CreateIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    log: str = Field(min_length=1)
    occurredAt: TimestampString
    source: str | SkipJsonSchema[None] = Field(
        default=None,
        max_length=100,
        validate_default=False,
        json_schema_extra=_remove_default,
    )

    @field_validator("source", mode="before")
    @classmethod
    def source_not_null(cls, value):
        return _reject_explicit_null(value)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["MATERIAL", "NOT_MATERIAL"]
    reviewer: str = Field(min_length=1)
    rationale: str | SkipJsonSchema[None] = Field(
        default=None,
        validate_default=False,
        json_schema_extra=_remove_default,
    )

    @field_validator("rationale", mode="before")
    @classmethod
    def rationale_not_null(cls, value):
        return _reject_explicit_null(value)


class LinkRequest(BaseModel):
    # Section 5.1 does not forbid additional properties on this request.
    # Accept them without persisting unrelated data or changing link meaning.
    model_config = ConfigDict(extra="ignore")

    relatedIncidentId: str = Field(min_length=1)
