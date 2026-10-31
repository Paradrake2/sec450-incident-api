"""Team 102 client contracts use only HTTPX mock transports, never live calls."""

import gzip
import json
import traceback
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from team102_client import Team102Client
from team102_contract import MAX_REPORT_BYTES, SourceApiError

_KEY = "opaque-test-credential"
_REPORT = b'{ "report_id": 42, "body": {"summary": "source-only"}, "extra": true }'


class ChunkedBody(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self):
        self.closed = True


def _transport_response(status=200, *, content=_REPORT, headers=None):
    response_headers = {"Content-Type": "application/json"}
    response_headers.update(headers or {})
    return httpx.MockTransport(lambda request: httpx.Response(
        status, content=content, headers=response_headers,
    ))


def test_fetch_uses_only_fixed_https_get_and_retains_raw_json_and_provenance():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://seclog.redinbo.win/v1/reports/42"
        assert request.url.query == b""
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        assert request.headers["Accept"] == "application/json"
        assert request.content == b""
        assert all(value == 10 for value in request.extensions["timeout"].values())
        return httpx.Response(200, content=_REPORT, headers={
            "Content-Type": "application/json; charset=utf-8", "X-Request-ID": "source-req:42",
        })

    before = datetime.now(UTC)
    with Team102Client(_KEY, transport=httpx.MockTransport(handler)) as client:
        assert _KEY not in repr(client)
        result = client.fetch_report(42)
    after = datetime.now(UTC)
    assert len(requests) == 1
    assert result.report_id == 42
    assert result.raw_content == _REPORT
    assert result.source_url == "https://seclog.redinbo.win/v1/reports/42"
    assert result.request_id == "source-req:42"
    assert result.retrieval_method == "api"
    assert before <= result.fetched_at <= after
    assert result.fetched_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("status,kind", [
    (301, "invalid_response"), (302, "invalid_response"), (307, "invalid_response"),
    (400, "upstream"), (401, "authentication"), (403, "authentication"),
    (404, "not_found"), (429, "rate_limit"), (500, "upstream"), (503, "upstream"),
    (204, "invalid_response"),
])
def test_statuses_are_sanitized_without_reading_error_bodies_or_following_redirects(status, kind):
    requests = []
    body = ChunkedBody([b"sensitive-body-secret"])

    def handler(request):
        requests.append(request)
        return httpx.Response(status, stream=body, headers={
            "Location": "https://attacker.invalid/leak", "Retry-After": "60",
        })

    with (
        Team102Client(_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(SourceApiError) as caught,
    ):
        client.fetch_report(42)
    assert caught.value.kind == kind
    assert caught.value.status_code == status
    assert len(requests) == 1
    assert body.read_count == 0
    assert body.closed
    details = "".join(traceback.format_exception(caught.value))
    assert _KEY not in details
    assert "sensitive-body-secret" not in details
    assert "attacker.invalid" not in details
    assert caught.value.retry_after_seconds == (60 if status == 429 else None)


@pytest.mark.parametrize("exception_type,kind", [
    (httpx.ConnectTimeout, "timeout"), (httpx.ReadTimeout, "timeout"),
    (httpx.ConnectError, "transport"), (httpx.RemoteProtocolError, "transport"),
    (httpx.DecodingError, "invalid_response"),
])
def test_transport_errors_do_not_leak_original_exception_details(exception_type, kind):
    requests = []

    def handler(request):
        requests.append(request)
        raise exception_type(f"{_KEY} sensitive-body-secret https://user:password@example.test", request=request)

    with (
        Team102Client(_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(SourceApiError) as caught,
    ):
        client.fetch_report(42)
    assert len(requests) == 1
    assert caught.value.kind == kind
    assert caught.value.__suppress_context__
    details = "".join(traceback.format_exception(caught.value))
    assert _KEY not in details
    assert "sensitive-body-secret" not in details
    assert "password" not in details


@pytest.mark.parametrize("retry_after,expected", [
    ("0", 0), ("60", 60), ("86400", 86400), ("86401", None),
    ("-1", None), ("1.5", None), ("soon", None), ("9" * 200, None),
])
def test_retry_after_seconds_metadata_is_bounded_and_no_retry_occurs(retry_after, expected):
    with Team102Client(_KEY, transport=_transport_response(
        429, headers={"Retry-After": retry_after},
    )) as client, pytest.raises(SourceApiError) as caught:
        client.fetch_report(42)
    assert caught.value.kind == "rate_limit"
    assert caught.value.retry_after_seconds == expected


def test_retry_after_http_date_is_accepted():
    retry_at = datetime.now(UTC) + timedelta(minutes=2)
    with Team102Client(_KEY, transport=_transport_response(
        429, headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
    )) as client, pytest.raises(SourceApiError) as caught:
        client.fetch_report(42)
    assert 118 <= caught.value.retry_after_seconds <= 120


@pytest.mark.parametrize("request_id", [
    "valid-request.id:42", "a" * 200, "a" * 201, "bad\r\nheader", "<script>", "two words", "",
])
def test_request_id_is_bounded_safe_metadata(request_id):
    with Team102Client(_KEY, transport=_transport_response(headers={
        "X-Request-ID": request_id,
    })) as client:
        result = client.fetch_report(42)
    expected = request_id if request_id in ("valid-request.id:42", "a" * 200) else None
    assert result.request_id == expected


@pytest.mark.parametrize("content_type", ["application/json", "Application/JSON; charset=UTF-8", "application/report+json"])
def test_json_media_types_are_accepted(content_type):
    with Team102Client(_KEY, transport=_transport_response(headers={
        "Content-Type": content_type,
    })) as client:
        assert client.fetch_report(42).raw_content == _REPORT


@pytest.mark.parametrize("headers", [{}, {"Content-Type": "text/html"}, {"Content-Type": "text/json"}])
def test_missing_or_non_json_content_type_is_rejected_without_body_read(headers):
    body = ChunkedBody([_REPORT])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=body, headers=headers))
    with (
        Team102Client(_KEY, transport=transport) as client,
        pytest.raises(SourceApiError, match="JSON") as caught,
    ):
        client.fetch_report(42)
    assert caught.value.kind == "invalid_response"
    assert body.read_count == 0
    assert body.closed


@pytest.mark.parametrize("raw_content", [
    b"not-json sensitive-body-secret", b"[]", b'{}', b'{"report_id": 42}',
    b'{"report_id": 43, "body": {}}', b'{"report_id": true, "body": {}}',
    b'{"report_id": "42", "body": {}}', b'{"report_id": 42, "body": []}',
    b'{"report_id": 42, "report_id": 42, "body": {}}',
    b'{"report_id": 42, "body": {"value": NaN}}', b'\xff',
])
def test_malformed_or_unsupported_reports_are_rejected_with_sanitized_errors(raw_content):
    with (
        Team102Client(_KEY, transport=_transport_response(content=raw_content)) as client,
        pytest.raises(SourceApiError) as caught,
    ):
        client.fetch_report(42)
    assert caught.value.kind == "invalid_response"
    assert "sensitive-body-secret" not in str(caught.value)
    assert _KEY not in str(caught.value)


def test_chunked_response_is_bounded_without_content_length_and_closes_early():
    body = ChunkedBody([b"x" * 65536, b"y" * 65536, b"never-read"])
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, stream=body, headers={"Content-Type": "application/json"},
    ))
    with (
        Team102Client(_KEY, transport=transport, max_response_bytes=65536) as client,
        pytest.raises(SourceApiError, match="size limit") as caught,
    ):
        client.fetch_report(42)
    assert caught.value.kind == "invalid_response"
    assert body.read_count == 2
    assert body.closed


def test_exact_response_limit_is_allowed():
    with Team102Client(_KEY, transport=_transport_response(), max_response_bytes=len(_REPORT)) as client:
        assert client.fetch_report(42).raw_content == _REPORT


def test_decompressed_response_is_bounded_not_just_compressed_length():
    raw = json.dumps({"report_id": 42, "body": {"padding": "x" * MAX_REPORT_BYTES}}).encode()
    encoded = gzip.compress(raw)
    assert len(encoded) < MAX_REPORT_BYTES
    body = ChunkedBody([encoded])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=body, headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip", "Content-Length": str(len(encoded)),
    }))
    with (
        Team102Client(_KEY, transport=transport) as client,
        pytest.raises(SourceApiError, match="size limit") as caught,
    ):
        client.fetch_report(42)
    assert caught.value.kind == "invalid_response"
    assert body.closed


@pytest.mark.parametrize("report_id", [-1, True, "42", "https://attacker.invalid/", "../reports", 10 ** 64])
def test_invalid_report_ids_are_rejected_before_transport(report_id):
    def handler(request):
        pytest.fail("Invalid report ID reached the transport")

    with (
        Team102Client(_KEY, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError),
    ):
        client.fetch_report(report_id)


@pytest.mark.parametrize("key", [None, "", "   ", "secret\r\nInjected: true", "secret\x00", "non-ascii-\u00e9"])
def test_invalid_credential_configuration_raises_without_echoing_key(key):
    with pytest.raises(ValueError) as caught:
        Team102Client(key, transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected request")))
    assert "secret" not in str(caught.value)
    assert "non-ascii" not in str(caught.value)


@pytest.mark.parametrize("kwargs", [
    {"timeout": 0}, {"timeout": -1}, {"timeout": float("inf")}, {"timeout": float("nan")},
    {"timeout": True}, {"max_response_bytes": 0}, {"max_response_bytes": True},
    {"max_response_bytes": MAX_REPORT_BYTES + 1},
])
def test_request_bounds_cannot_be_disabled(kwargs):
    with pytest.raises(ValueError):
        Team102Client(_KEY, **kwargs)
