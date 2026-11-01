"""Bounded, manual-only access to Team 102's read-only report and log feeds.

Shape-neutral reads support inspecting a single source sample. Imports retain
the provisional identity/body boundary in team102_contract; no polling
checkpoint or report update behavior is assumed here.
"""

import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Self

import httpx

from team102_contract import (
    MAX_REPORT_BYTES,
    ReportFetch,
    SOURCE_BASE_URL,
    SourceApiError,
    SourceRead,
    decode_source_json,
    parse_report,
    validate_report_id,
)

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_SAFE_REPORT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_MAX_RETRY_AFTER_SECONDS = 24 * 60 * 60


def _retry_after_seconds(value: str | None) -> int | None:
    """Keep bounded delay metadata; callers must decide when to retry manually."""
    if value is None or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        delay = int(value)
    else:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return None
            delay = max(0, math.ceil(
                (retry_at - datetime.now(UTC)).total_seconds()
            ))
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if 0 <= delay <= _MAX_RETRY_AFTER_SECONDS else None


def _check_status(response: httpx.Response) -> None:
    """Classify using status/headers only: error bodies are never read."""
    status = response.status_code
    if status == 200:
        return
    if status in (401, 403):
        kind, message = "authentication", "Team 102 rejected the API credentials"
    elif status == 404:
        kind, message = "not_found", "Team 102 did not find that source resource"
    elif status == 429:
        raise SourceApiError(
            "rate_limit", "Team 102 rate limited the request",
            status_code=status,
            retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
        )
    elif 400 <= status <= 599:
        kind, message = "upstream", "Team 102 could not complete the source request"
    else:
        kind, message = "invalid_response", "Team 102 returned an unsupported HTTP response"
    raise SourceApiError(kind, message, status_code=status)


class Team102Client:
    """Read source reports/logs; never follows redirects or retries.

    Credentials are opaque header values: no key-prefix assumption is made.
    TLS verification stays enabled; proxy/environment settings are not trusted.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10,
        max_response_bytes: int = MAX_REPORT_BYTES,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("A nonempty Team 102 API key is required")
        try:
            encoded_key = api_key.encode("ascii")
        except UnicodeError:
            raise ValueError("The Team 102 API key must be a valid HTTP header value") from None
        if any(value < 32 or value == 127 for value in encoded_key):
            raise ValueError("The Team 102 API key must be a valid HTTP header value")
        if (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if (
            type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= MAX_REPORT_BYTES
        ):
            raise ValueError(f"max_response_bytes must be between 1 and {MAX_REPORT_BYTES}")
        self._max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_report(self, report_id: int) -> ReportFetch:
        """Fetch an importable report using the guide's numeric ID/body format."""
        validate_report_id(report_id)
        result = self.read_report(report_id)
        parse_report(result.raw_content, report_id)
        return ReportFetch(
            report_id=report_id,
            raw_content=result.raw_content,
            fetched_at=result.fetched_at,
            source_url=result.source_url,
            request_id=result.request_id,
        )

    def read_report(self, report_id: str | int) -> SourceRead:
        """Read one report without assuming its record schema or numeric ID.

        Only a bounded ASCII path segment is accepted, including UUIDs. URLs,
        separators, percent escapes, and query strings cannot be supplied.
        """
        if type(report_id) is int:
            if report_id < 0:
                raise ValueError("report_id must be a safe source identifier")
            report_id = str(report_id)
        if not isinstance(report_id, str) or _SAFE_REPORT_ID.fullmatch(report_id) is None:
            raise ValueError("report_id must be a safe source identifier")
        return self._read(f"{SOURCE_BASE_URL}/v1/reports/{report_id}")

    def read_reports_sample(self) -> SourceRead:
        """Read a list sample with limit=1; do not infer or traverse its shape."""
        return self._read(f"{SOURCE_BASE_URL}/v1/reports?limit=1")

    def read_logs_sample(self) -> SourceRead:
        """Read a log list sample without mapping its entries to reports."""
        return self._read(f"{SOURCE_BASE_URL}/v1/logs?limit=1")

    def _read(self, url: str) -> SourceRead:
        """Apply the same transport bounds before examining any JSON shape."""
        try:
            with self._client.stream("GET", url) as response:
                _check_status(response)
                media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type != "application/json" and not (
                    media_type.startswith("application/") and media_type.endswith("+json")
                ):
                    raise SourceApiError(
                        "invalid_response", "Team 102 did not return JSON"
                    )
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(chunk) > self._max_response_bytes - len(content):
                        raise SourceApiError(
                            "invalid_response", "Team 102 source response exceeds the size limit"
                        )
                    content.extend(chunk)
                raw_content = bytes(content)
                decode_source_json(raw_content)
                request_id = response.headers.get("X-Request-ID")
                if request_id is not None and _SAFE_REQUEST_ID.fullmatch(request_id) is None:
                    request_id = None
                return SourceRead(
                    raw_content=raw_content,
                    fetched_at=datetime.now(UTC),
                    source_url=url,
                    request_id=request_id,
                )
        except httpx.TimeoutException:
            raise SourceApiError("timeout", "Team 102 source request timed out") from None
        except httpx.DecodingError:
            raise SourceApiError(
                "invalid_response", "Team 102 returned an invalid encoded response"
            ) from None
        except httpx.RequestError:
            raise SourceApiError("transport", "Team 102 source request could not connect") from None
