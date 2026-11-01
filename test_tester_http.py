"""Live-gateway policy tests are offline and use synthetic network backends."""

import asyncio
import json
import socket
import ssl

import httpcore
import httpx
import pytest

from tester_http import HttpTargetGateway, PublicHTTPTransport, PublicNetworkBackend
from tester_interfaces import ProtocolFailure


TARGET = "https://approved.example/system"
SID = "synthetic-session-1"


def invoke(awaitable):
    return asyncio.run(awaitable)


def gateway(handler, **kwargs):
    return HttpTargetGateway(
        (TARGET,), transport=httpx.MockTransport(handler), **kwargs
    )


def test_setup_posts_to_approved_base_path_and_confirms_exact_session_without_credentials(
    monkeypatch,
):
    monkeypatch.setenv("HTTPS_PROXY", "http://private-proxy.invalid")
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == TARGET + "/test/setup"
        assert json.loads(request.content) == {"session_id": SID, "setup": {}}
        assert request.headers["x-test-session-id"] == SID
        assert request.headers["accept-encoding"] == "identity"
        assert not {"authorization", "cookie", "x-api-key"} & set(request.headers)
        assert all(value == 10 for value in request.extensions["timeout"].values())
        return httpx.Response(201, json={"session_id": SID})

    assert invoke(gateway(handler).prepare(TARGET + "/", SID)) == SID
    assert len(requests) == 1


def test_coverage_validates_session_and_preserves_unknown_counts():
    def handler(request):
        assert request.url.path == "/system/test/report"
        assert dict(request.url.params) == {"session_id": SID}
        return httpx.Response(
            200,
            json={
                "session_id": SID,
                "coverage": {
                    "code": {
                        "covered": None,
                        "total": None,
                        "basis": "Not instrumented",
                    }
                },
            },
        )

    measure = invoke(gateway(handler).code_coverage(TARGET, SID))
    assert measure.covered is measure.total is None
    assert measure.basis == "Not instrumented"


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "other"},
        {"session_id": 42},
        {"session_id": SID, "private": "PRIVATE"},
        [],
        {},
    ],
)
def test_invalid_or_mismatched_setup_response_is_sanitized(payload):
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(lambda request: httpx.Response(201, json=payload)).prepare(
                TARGET, SID
            )
        )
    assert error.value.status_code == 502
    assert error.value.source == "system"
    assert "PRIVATE" not in error.value.message


@pytest.mark.parametrize(
    "body",
    [
        b'{"session_id":"synthetic-session-1","session_id":"synthetic-session-1"}',
        b'{"session_id":NaN}',
        b'{"session_id":"\\ud800"}',
        b"PRIVATE invalid JSON",
    ],
)
def test_ambiguous_or_invalid_setup_json_is_rejected_without_values(body):
    def response(request):
        return httpx.Response(
            201, content=body, headers={"content-type": "application/json"}
        )

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(response).prepare(TARGET, SID))
    assert error.value.source == "system"
    assert "PRIVATE" not in error.value.message


@pytest.mark.parametrize(
    "payload",
    [
        {
            "session_id": "other",
            "coverage": {"code": {"covered": 1, "total": 2, "basis": "Measured"}},
        },
        {
            "session_id": SID,
            "coverage": {"code": {"covered": True, "total": 2, "basis": "PRIVATE"}},
        },
        {
            "session_id": SID,
            "coverage": {"code": {"covered": 3, "total": 2, "basis": "PRIVATE"}},
        },
        {
            "session_id": SID,
            "coverage": {"code": {"covered": None, "total": 2, "basis": "PRIVATE"}},
        },
    ],
)
def test_invalid_coverage_response_does_not_become_fabricated_counts(payload):
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(lambda request: httpx.Response(200, json=payload)).code_coverage(
                TARGET, SID
            )
        )
    assert error.value.status_code == 502
    assert error.value.source == "system"
    assert "PRIVATE" not in error.value.message


def test_valid_public_protocol_failure_preserves_status_message_and_source():
    def denied(request):
        return httpx.Response(
            403,
            json={
                "message": "Target denied this test session",
                "source": "communication",
            },
        )

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(denied).prepare(TARGET, SID))
    assert (error.value.status_code, error.value.message, error.value.source) == (
        403,
        "Target denied this test session",
        "communication",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"private": "PRIVATE"},
        {"message": "PRIVATE", "source": "not-a-source"},
        {"message": "PRIVATE", "source": "system", "extra": True},
        {"message": "Authorization: Bearer PRIVATE", "source": "system"},
        {"message": "token=PRIVATE", "source": "system"},
        {"message": "Traceback (PRIVATE)", "source": "system"},
        {"message": "PRIVATE\nsecond line", "source": "system"},
        {"message": "PRIVATE" * 400, "source": "system"},
    ],
)
def test_suspect_or_malformed_error_body_is_not_reflected(payload):
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(lambda request: httpx.Response(400, json=payload)).prepare(
                TARGET, SID
            )
        )
    assert error.value.status_code == 400
    assert "PRIVATE" not in error.value.message


def test_coverage_control_failure_preserves_valid_classified_error():
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(
                lambda request: httpx.Response(
                    404, json={"message": "Unknown test session", "source": "system"}
                )
            ).code_coverage(TARGET, SID)
        )
    assert (error.value.status_code, error.value.message, error.value.source) == (
        404,
        "Unknown test session",
        "system",
    )


@pytest.mark.parametrize(
    "target",
    [
        "https://unapproved.example/system",
        TARGET + "/more",
        "http://approved.example/system",
        "https://user:PRIVATE@approved.example/system",
        "https://127.0.0.1/system",
    ],
)
def test_unapproved_target_cannot_reach_transport(target):
    def forbidden(request):
        pytest.fail("Rejected target reached transport")

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(forbidden).prepare(target, SID))
    assert error.value.status_code == 400
    assert "PRIVATE" not in error.value.message


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/path",
        "//evil.example/path",
        "/../secret",
        "/a/./b",
        "/a//b",
        "/%2e%2e/private",
        "/\\evil",
        "/path?api_key=PRIVATE",
        "/path#PRIVATE",
        "/bad\npath",
        "/bad\u200bpath",
    ],
)
def test_project_path_cannot_replace_authority_or_escape_base(path):
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(
                lambda request: pytest.fail("Invalid path reached transport")
            ).request(TARGET, SID, "GET", path)
        )
    assert error.value.source == "tester"
    assert "PRIVATE" not in error.value.message


@pytest.mark.parametrize("session", ["", " ", "PRIVATE\nvalue", "x" * 129, None])
def test_invalid_session_cannot_become_a_header(session):
    with pytest.raises(ProtocolFailure):
        invoke(
            gateway(
                lambda request: pytest.fail("Invalid session reached transport")
            ).request(TARGET, session, "GET", "/health")
        )


def test_case_requests_support_controlled_media_errors_without_custom_credential_headers():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/system/api/v1/incidents"
        assert request.headers["content-type"] == "text/plain"
        assert request.content == b"synthetic"
        return httpx.Response(
            415, json={"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Use JSON"}
        )

    adapter = gateway(handler)
    response = invoke(
        adapter.request(
            TARGET,
            SID,
            "POST",
            "/api/v1/incidents",
            content="synthetic",
            headers={"Content-Type": "text/plain"},
        )
    )
    assert response.status_code == 415
    with pytest.raises(ProtocolFailure):
        invoke(
            adapter.request(
                TARGET,
                SID,
                "GET",
                "/health",
                headers={"Authorization": "Bearer PRIVATE"},
            )
        )


def test_project_server_failure_is_classified_without_its_body():
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(
                lambda request: httpx.Response(503, json={"private": "PRIVATE"})
            ).request(TARGET, SID, "GET", "/health")
        )
    assert error.value.status_code == 503 and error.value.source == "system"
    assert "PRIVATE" not in error.value.message


def test_redirects_are_rejected_and_not_followed():
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(
            307, headers={"location": "https://private.invalid/PRIVATE"}
        )

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(redirect).prepare(TARGET, SID))
    assert len(calls) == 1
    assert error.value.source == "system" and "PRIVATE" not in error.value.message


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.reads, self.closed = chunks, 0, False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def test_response_limit_closes_stream_and_does_not_return_partial_payload():
    stream = CountingStream([b"PRIVATE" * 20])
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(
                lambda request: httpx.Response(200, stream=stream),
                max_response_bytes=20,
            ).request(TARGET, SID, "GET", "/health")
        )
    assert stream.closed
    assert error.value.status_code == 502 and error.value.source == "system"
    assert "PRIVATE" not in error.value.message


def test_unsolicited_compression_is_rejected_before_reading_or_decoding():
    stream = CountingStream([b"PRIVATE compressed body"])
    with pytest.raises(ProtocolFailure):
        invoke(
            gateway(
                lambda request: httpx.Response(
                    200, headers={"content-encoding": "gzip"}, stream=stream
                )
            ).request(TARGET, SID, "GET", "/health")
        )
    assert stream.closed and stream.reads == 0


class CloseFailureStream(CountingStream):
    async def aclose(self):
        self.closed = True
        raise httpx.ReadError("PRIVATE cleanup detail")


def test_response_cleanup_does_not_mask_an_existing_policy_failure():
    stream = CloseFailureStream([])
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(lambda request: httpx.Response(307, stream=stream)).prepare(
                TARGET, SID
            )
        )
    assert stream.closed
    assert error.value.source == "system"
    assert error.value.message == "Target redirects are not permitted"


def test_response_cleanup_failure_on_success_is_sanitized_communication_error():
    stream = CloseFailureStream([b"{}"])
    with pytest.raises(ProtocolFailure) as error:
        invoke(
            gateway(lambda request: httpx.Response(200, stream=stream)).request(
                TARGET, SID, "GET", "/health"
            )
        )
    assert stream.closed
    assert error.value.source == "communication"
    assert "PRIVATE" not in error.value.message


@pytest.mark.parametrize(
    "failure", [httpcore.ReadTimeout, httpcore.ReadError, httpcore.RemoteProtocolError]
)
def test_core_stream_cleanup_errors_are_mapped_without_details(failure):
    from tester_http import _CoreStream

    class Failing:
        async def aclose(self):
            raise failure("PRIVATE core detail")

    with pytest.raises(httpx.HTTPError) as error:
        invoke(_CoreStream(Failing()).aclose())
    assert "PRIVATE" not in str(error.value)


def test_pool_cleanup_does_not_mask_a_primary_exception():
    class FailingPool:
        async def aclose(self):
            raise httpcore.ReadError("PRIVATE pool detail")

    transport = PublicHTTPTransport()
    transport._pool = FailingPool()
    primary = ProtocolFailure(400, "Keep original policy error", "tester")
    invoke(transport.__aexit__(ProtocolFailure, primary, None))
    with pytest.raises(httpx.HTTPError) as error:
        invoke(transport.aclose())
    assert "PRIVATE" not in str(error.value)


def test_total_timeout_bounds_a_slow_target_and_does_not_retry():
    calls = []

    async def slow(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200)

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(slow, timeout=0.01).request(TARGET, SID, "GET", "/health"))
    assert len(calls) == 1
    assert error.value.status_code == 504 and error.value.source == "communication"


def test_network_exception_is_sanitized():
    def failed(request):
        raise httpx.ConnectError("PRIVATE connection info")

    with pytest.raises(ProtocolFailure) as error:
        invoke(gateway(failed).prepare(TARGET, SID))
    assert (
        error.value.source == "communication" and "PRIVATE" not in error.value.message
    )


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self):
        self.hostnames, self.contexts, self.writes = [], [], []
        self.response = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 15\r\n\r\n{"status":"ok"}'
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        response, self.response = self.response, b""
        return response

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.hostnames.append(server_hostname)
        self.contexts.append(ssl_context)
        return self

    async def aclose(self):
        self.closed = True


class FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.calls, self.stream = [], FakeStream()

    async def connect_tcp(self, host, port, **kwargs):
        self.calls.append((host, port, kwargs))
        return self.stream


def test_public_backend_pins_ip_but_core_preserves_original_host_and_verified_tls_name():
    backend = FakeBackend()
    resolved = []

    def resolver(host, port):
        resolved.append((host, port))
        return ["8.8.8.8", "2001:4860:4860::8888"]

    transport = PublicHTTPTransport(
        network_backend=PublicNetworkBackend(delegate=backend, resolver=resolver)
    )

    async def request():
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            # Untrusted extensions cannot replace the checked target or SNI.
            return await client.get(
                "https://approved.example/health",
                extensions={"sni_hostname": "PRIVATE", "target": b"/PRIVATE"},
            )

    response = invoke(request())
    assert response.json() == {"status": "ok"}
    assert resolved == [("approved.example", 443)]
    assert backend.calls[0][0:2] == ("8.8.8.8", 443)
    assert backend.stream.hostnames == ["approved.example"]
    assert backend.stream.contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert backend.stream.contexts[0].check_hostname is True
    wire = b"".join(backend.stream.writes)
    assert b"Host: approved.example" in wire and b"GET /health" in wire
    assert b"PRIVATE" not in wire and backend.stream.closed


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["::1"],
        ["224.0.0.1"],
        ["0.0.0.0"],
        ["8.8.8.8", "192.168.1.1"],
        ["not-an-address"],
    ],
)
def test_private_or_mixed_dns_answers_are_rejected_before_a_socket(answers):
    backend = FakeBackend()
    guarded = PublicNetworkBackend(
        delegate=backend, resolver=lambda host, port: answers
    )
    transport = PublicHTTPTransport(network_backend=guarded)
    adapter = HttpTargetGateway((TARGET,), transport=transport)
    with pytest.raises(ProtocolFailure) as error:
        invoke(adapter.request(TARGET, SID, "GET", "/health"))
    assert backend.calls == []
    assert error.value.status_code == 400 and error.value.source == "tester"
    invoke(transport.aclose())


def test_dns_failure_is_communication_failure_without_host_or_exception_leak():
    def resolver(host, port):
        raise socket.gaierror("PRIVATE resolver detail")

    backend = FakeBackend()
    transport = PublicHTTPTransport(
        network_backend=PublicNetworkBackend(delegate=backend, resolver=resolver)
    )
    with pytest.raises(ProtocolFailure) as error:
        invoke(HttpTargetGateway((TARGET,), transport=transport).prepare(TARGET, SID))
    assert backend.calls == []
    assert (
        error.value.source == "communication" and "PRIVATE" not in error.value.message
    )
    invoke(transport.aclose())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"timeout": float("inf")},
        {"timeout": True},
        {"max_response_bytes": 0},
        {"max_response_bytes": True},
        {"max_response_bytes": 2**22},
    ],
)
def test_invalid_bounds_are_rejected_at_construction(kwargs):
    with pytest.raises((ValueError, TypeError)):
        HttpTargetGateway((TARGET,), **kwargs)


def test_live_configuration_requires_https_even_if_offline_admission_accepts_http():
    with pytest.raises(ValueError):
        HttpTargetGateway(("http://approved.example/system",))


@pytest.mark.parametrize(
    "local", ["http://127.0.0.1:8002/system", "http://[::1]:8002/system"]
)
def test_explicit_local_gateway_can_use_exact_loopback_http_with_base_path(local):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == local + "/test/setup"
        return httpx.Response(201, json={"session_id": SID})

    with pytest.raises(ValueError):
        HttpTargetGateway((local,))
    adapter = HttpTargetGateway(
        (local,), allow_loopback=True, transport=httpx.MockTransport(handler)
    )
    assert invoke(adapter.prepare(local, SID)) == SID
    assert len(requests) == 1


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_gateway_loopback_flag_is_a_constructor_boolean(value):
    with pytest.raises(ValueError):
        HttpTargetGateway((TARGET,), allow_loopback=value)


def test_local_mode_never_permits_plain_http_to_a_public_target():
    with pytest.raises(ValueError):
        HttpTargetGateway(("http://approved.example/system",), allow_loopback=True)


def test_local_http_transport_connects_to_literal_without_dns_or_tls():
    local = "http://127.0.0.1:8002/system"
    backend = FakeBackend()

    def no_dns(*args):
        pytest.fail("An approved loopback literal must not require DNS")

    guarded = PublicNetworkBackend(
        delegate=backend, resolver=no_dns, allowed_loopback=("127.0.0.1",)
    )
    transport = PublicHTTPTransport(
        network_backend=guarded, allowed_loopback=("127.0.0.1",)
    )
    adapter = HttpTargetGateway((local,), allow_loopback=True, transport=transport)
    response = invoke(adapter.request(local, SID, "GET", "/health"))
    assert response.json() == {"status": "ok"}
    assert backend.calls[0][0:2] == ("127.0.0.1", 8002)
    assert backend.stream.hostnames == []
    wire = b"".join(backend.stream.writes)
    assert b"GET /system/health" in wire and b"Host: 127.0.0.1:8002" in wire
    invoke(transport.aclose())


@pytest.mark.parametrize("answers", [["127.0.0.1"], ["::1"], ["8.8.8.8", "127.0.0.1"]])
def test_public_target_cannot_rebind_to_an_approved_loopback_address_in_local_mode(
    answers,
):
    backend = FakeBackend()
    guarded = PublicNetworkBackend(
        delegate=backend,
        resolver=lambda host, port: answers,
        allowed_loopback=("127.0.0.1", "::1"),
    )
    transport = PublicHTTPTransport(
        network_backend=guarded, allowed_loopback=("127.0.0.1", "::1")
    )
    adapter = HttpTargetGateway(
        (TARGET, "http://127.0.0.1:8002/system"),
        allow_loopback=True,
        transport=transport,
    )
    with pytest.raises(ProtocolFailure) as error:
        invoke(adapter.request(TARGET, SID, "GET", "/health"))
    assert backend.calls == []
    assert error.value.status_code == 400 and error.value.source == "tester"
    invoke(transport.aclose())


@pytest.mark.parametrize(
    "allowed", [("localhost",), ("10.0.0.1",), ["127.0.0.1"], ("127.0.0.2",)]
)
def test_transport_loopback_exception_only_accepts_exact_literal_configuration(allowed):
    with pytest.raises(ValueError):
        PublicNetworkBackend(allowed_loopback=allowed)
    with pytest.raises(ValueError):
        PublicHTTPTransport(allowed_loopback=allowed)
