"""Wire models for the course's cross-team testing protocol.

Coverage with no available measurement is represented by two explicit null
counts and an explanation in ``basis``. It must not be reported as zero.
"""

from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Must contain non-whitespace characters")
    return value


NonblankString = Annotated[
    str, Field(strict=True, min_length=1), AfterValidator(_nonblank)
]
CoverageCount = Annotated[int, Field(strict=True, ge=0)]
ErrorSource = Literal["tester", "system", "communication", "unknown"]
TestOutcome = Literal["pass", "fail", "error", "skip"]


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProtocolErrorBody(ProtocolModel):
    message: NonblankString
    source: ErrorSource


class SessionResponse(ProtocolModel):
    session_id: NonblankString


class SystemSetupRequest(ProtocolModel):
    session_id: NonblankString
    setup: dict


class TesterSetupRequest(ProtocolModel):
    target_url: NonblankString


class TesterRunRequest(ProtocolModel):
    session_id: NonblankString


class CoverageMeasure(ProtocolModel):
    covered: CoverageCount | None
    total: CoverageCount | None
    basis: NonblankString

    @model_validator(mode="after")
    def validate_counts(self) -> "CoverageMeasure":
        if (self.covered is None) != (self.total is None):
            raise ValueError("Coverage counts must both be integers or both be null")
        if self.covered is not None and self.total is not None and self.covered > self.total:
            raise ValueError("Covered count must not exceed total count")
        return self


class SystemCoverage(ProtocolModel):
    code: CoverageMeasure


class SuiteCoverage(ProtocolModel):
    code: CoverageMeasure
    component: CoverageMeasure
    system: CoverageMeasure


class SystemReport(ProtocolModel):
    session_id: NonblankString
    coverage: SystemCoverage


class TestResult(ProtocolModel):
    test_id: NonblankString
    outcome: TestOutcome
    message: NonblankString
    source: ErrorSource | None = None

    @model_validator(mode="after")
    def require_error_source(self) -> "TestResult":
        if self.outcome == "error" and self.source is None:
            raise ValueError("Error results require a source")
        return self

    @model_serializer(mode="wrap")
    def serialize_result(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = handler(self)
        if self.source is None:
            result.pop("source", None)
        return result


class SuiteReport(ProtocolModel):
    session_id: NonblankString
    target_url: NonblankString
    results: list[TestResult]
    coverage: SuiteCoverage

    @model_validator(mode="after")
    def require_unique_test_ids(self) -> "SuiteReport":
        ids = [result.test_id for result in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError("Report result test IDs must be unique")
        return self
