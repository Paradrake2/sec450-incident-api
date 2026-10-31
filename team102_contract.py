"""Minimal, provisional report boundary from Team 102's supplied Query API guide."""

from dataclasses import dataclass
from datetime import datetime
import json


SOURCE_BASE_URL = "https://seclog.redinbo.win"
MAX_REPORT_BYTES = 2 * 1024 * 1024


class SourceApiError(Exception):
    """Sanitized integration failure; never include response bodies or credentials."""

    def __init__(
        self, kind: str, message: str, *, status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def validate_report_id(report_id: int) -> int:
    if type(report_id) is not int or report_id < 0 or len(str(report_id)) > 64:
        raise ValueError("report_id must be a nonnegative integer with at most 64 digits")
    return report_id


def report_url(report_id: int) -> str:
    return f"{SOURCE_BASE_URL}/v1/reports/{validate_report_id(report_id)}"


@dataclass(frozen=True)
class ReportFetch:
    report_id: int
    raw_content: bytes
    fetched_at: datetime
    source_url: str
    request_id: str | None = None
    retrieval_method: str = "api"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON number")


def parse_report(raw_content: bytes, expected_report_id: int) -> dict:
    """Validate only the guide's report identity/body; retain unknown fields.

    The complete detail-response schema still needs a sanitized source example.
    This parser does not infer occurrence time, impact, or a human decision.
    """
    validate_report_id(expected_report_id)
    try:
        if not isinstance(raw_content, bytes) or len(raw_content) > MAX_REPORT_BYTES:
            raise ValueError("Report is not bounded bytes")
        document = json.loads(
            raw_content.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(document, dict):
            raise ValueError("Report must be an object")
        report_id = document.get("report_id")
        validate_report_id(report_id)
        if report_id != expected_report_id or not isinstance(document.get("body"), dict):
            raise ValueError("Report identity or body does not match")
        # Reject escaped lone surrogates and numeric overflow as well as the
        # non-standard constants rejected by the decoder above.
        canonical_report(document)
        return document
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise SourceApiError(
            "invalid_response", "Team 102 returned an unsupported report response"
        ) from None


def canonical_report(document: dict) -> bytes:
    """Ignore JSON layout/key order for duplicate detection, not source changes."""
    return json.dumps(
        document, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
