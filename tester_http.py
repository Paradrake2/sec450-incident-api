"""Bounded HTTPS transport for explicitly approved course-test targets.

Live sockets connect only to public IPs checked at connection time. The
original host remains the HTTP authority and TLS/SNI name. Injected transports
are a trusted-code seam for offline tests, not caller-selected configuration.
Trusted local factories may separately approve exact loopback IP literals and
HTTP for a real local walkthrough; public-host DNS checks remain unchanged.
"""

import asyncio
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from ipaddress import ip_address
import json as json_module
import math
import re
import socket
from urllib.parse import urlsplit

import anyio
import httpcore
import httpx
from pydantic import ValidationError

from tester_interfaces import ProtocolFailure
from tester_targets import ApprovedTargets
from testing_protocol import ProtocolErrorBody, SessionResponse, SystemReport


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(?:authorization\s*:|bearer\s+\S+|sec450_[A-Za-z0-9]+|"
    r"(?:password|token|api[_ -]?key)\s*[:=]\s*\S+|traceback\s*\()"
)
_UNSET = object()
_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1"})


class _AddressPolicyError(Exception):
    pass


def _public_address(value):
    address = ip_address(value)
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise _AddressPolicyError
    return str(address)


def _resolve(host, port):
    return [
        row[4][0]
        for row in socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    ]


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, reject mixed/private answers, and connect to a literal IP.

    The core pool supplies the original name separately when it starts TLS.
    A later DNS change cannot redirect this checked connection to another IP.
    A loopback exception applies only to explicitly approved literal hosts,
    never to DNS results for a public name.
    """

    def __init__(self, *, delegate=None, resolver=None, allowed_loopback=()):
        if not isinstance(allowed_loopback, tuple) or any(
            address not in _LOOPBACK_LITERALS for address in allowed_loopback
        ):
            raise ValueError(
                "Only exact approved loopback literals may bypass public address checks"
            )
        self._delegate = delegate if delegate is not None else httpcore.AnyIOBackend()
        self._resolver = resolver if resolver is not None else _resolve
        self._allowed_loopback = frozenset(allowed_loopback)

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,
        local_address=None,
        socket_options=None,
    ):
        try:
            address = ip_address(host)
        except ValueError:
            # Cancellation returns promptly even if an OS resolver is slow.
            candidates = await anyio.to_thread.run_sync(
                self._resolver,
                host,
                port,
                abandon_on_cancel=True,
            )
            allow_literal = False
        else:
            candidates = [str(address)]
            allow_literal = str(address) in self._allowed_loopback
        if not candidates:
            raise httpcore.ConnectError("Target DNS resolution returned no addresses")
        try:
            # A public/DNS hostname may never use this exception, including
            # when it resolves to the same address as an approved local target.
            checked = (
                set(candidates)
                if allow_literal
                else {_public_address(value) for value in candidates}
            )
        except (ValueError, _AddressPolicyError):
            raise _AddressPolicyError from None
        selected = min(checked, key=lambda value: (ip_address(value).version, value))
        return await self._delegate.connect_tcp(
            selected,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise _AddressPolicyError

    async def sleep(self, seconds):
        await self._delegate.sleep(seconds)


def _transport_error(exc):
    if isinstance(exc, httpcore.TimeoutException):
        return httpx.TimeoutException("Target network operation timed out")
    if isinstance(exc, httpcore.ProtocolError):
        return httpx.RemoteProtocolError("Target network protocol failed")
    return httpx.ConnectError("Target network operation failed")


class _CoreStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self._stream = stream

    async def __aiter__(self):
        try:
            async for chunk in self._stream:
                yield chunk
        except (
            httpcore.TimeoutException,
            httpcore.NetworkError,
            httpcore.ProtocolError,
        ) as exc:
            raise _transport_error(exc) from None

    async def aclose(self):
        if hasattr(self._stream, "aclose"):
            try:
                await self._stream.aclose()
            except (
                httpcore.TimeoutException,
                httpcore.NetworkError,
                httpcore.ProtocolError,
            ) as exc:
                raise _transport_error(exc) from None


class PublicHTTPTransport(httpx.AsyncBaseTransport):
    """HTTPX transport using the public HTTPcore pool/network-backend APIs."""

    def __init__(self, *, network_backend=None, allowed_loopback=()):
        if not isinstance(allowed_loopback, tuple) or any(
            address not in _LOOPBACK_LITERALS for address in allowed_loopback
        ):
            raise ValueError(
                "Only exact approved loopback literals may use local transport"
            )
        self._allowed_loopback = frozenset(allowed_loopback)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            network_backend=(
                network_backend
                if network_backend is not None
                else PublicNetworkBackend(allowed_loopback=allowed_loopback)
            ),
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            retries=0,
        )

    async def handle_async_request(self, request):
        if request.url.scheme != "https" and not (
            request.url.scheme == "http" and request.url.host in self._allowed_loopback
        ):
            raise httpx.UnsupportedProtocol("Test targets must use HTTPS")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            # No extension may replace the checked URL target or TLS name.
            extensions={"timeout": request.extensions.get("timeout", {})},
        )
        try:
            response = await self._pool.handle_async_request(core_request)
        except (
            httpcore.TimeoutException,
            httpcore.NetworkError,
            httpcore.ProtocolError,
        ) as exc:
            raise _transport_error(exc) from None
        if not isinstance(response.stream, AsyncIterable):
            raise httpx.RemoteProtocolError(
                "Target returned an invalid response stream"
            )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        try:
            await self._pool.aclose()
        except (
            httpcore.TimeoutException,
            httpcore.NetworkError,
            httpcore.ProtocolError,
        ) as exc:
            raise _transport_error(exc) from None

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            await self.aclose()
        except (httpx.HTTPError, OSError):
            if exc_value is None:
                raise


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Do not close a transport whose lifecycle belongs to trusted test code."""

    def __init__(self, transport):
        self._transport = transport

    async def handle_async_request(self, request):
        return await self._transport.handle_async_request(request)


@asynccontextmanager
async def _response_stream(client, method, url, **kwargs):
    """Close a response without allowing cleanup to mask an existing failure."""
    request = client.build_request(method, url, **kwargs)
    response = await client.send(request, stream=True)
    try:
        yield response
    except BaseException:
        try:
            await response.aclose()
        except BaseException:
            # Preserve the original assertion/policy error or cancellation.
            pass
        raise
    else:
        await response.aclose()


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON value")


def _protocol_json(response):
    if (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ValueError("Protocol response must use application/json")
    value = json_module.loads(
        response.content.decode("utf-8"),
        object_pairs_hook=_unique_fields,
        parse_constant=_invalid_constant,
    )
    json_module.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return value


def _control_failure(response, fallback):
    status = response.status_code if 400 <= response.status_code <= 599 else 502
    message, source = fallback, "system"
    try:
        body = ProtocolErrorBody.model_validate(_protocol_json(response))
        candidate = body.message
        if (
            len(candidate) <= 2048
            and not any(ord(char) < 32 or ord(char) == 127 for char in candidate)
            and _CREDENTIAL_TEXT.search(candidate) is None
        ):
            message, source = candidate, body.source
    except (ValueError, TypeError, RecursionError):
        pass
    return ProtocolFailure(status, message, source)


def _relative_path(value):
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "//" in value
        or any(char in value for char in "\\%?#")
        or any(char.isspace() or ord(char) < 32 or ord(char) >= 127 for char in value)
        or any(part in {".", ".."} for part in value.split("/"))
    ):
        raise ProtocolFailure(400, "Invalid project request path", "tester")
    return value


class HttpTargetGateway:
    """One bounded request per call; no proxies, redirects or retries.

    A fresh live transport per request avoids retained sockets and cross-loop
    pools. Production permits only explicitly approved HTTPS bases. A trusted
    local factory may enable exact loopback literals/HTTP; request bodies cannot
    enable that mode. No API-key or caller Authorization forwarding is provided.
    """

    def __init__(
        self,
        approved_targets: tuple[str, ...],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        allow_loopback: bool = False,
    ):
        self._targets = ApprovedTargets(approved_targets, allow_loopback=allow_loopback)
        self._allowed_loopback = tuple(
            sorted(
                {
                    urlsplit(target).hostname
                    for target in self._targets.targets
                    if allow_loopback
                    and urlsplit(target).hostname in _LOOPBACK_LITERALS
                }
            )
        )
        if any(
            not target.startswith("https://")
            and urlsplit(target).hostname not in self._allowed_loopback
            for target in self._targets.targets
        ):
            raise ValueError("Live test targets must use HTTPS")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 120
        ):
            raise ValueError("timeout must be finite and between zero and 120 seconds")
        if (
            type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES
        ):
            raise ValueError("max_response_bytes is out of bounds")
        if transport is not None and not isinstance(
            transport, httpx.AsyncBaseTransport
        ):
            raise TypeError("Injected transport must be an async HTTPX transport")
        self._transport = transport
        self._timeout = float(timeout)
        self._max_response_bytes = max_response_bytes

    async def _request(
        self,
        target_url,
        session_id,
        method,
        relative_path,
        *,
        json=_UNSET,
        params=None,
        content=None,
        headers=None,
    ):
        target = self._targets.normalize_and_require(target_url)
        path = _relative_path(relative_path)
        if not isinstance(session_id, str) or _SESSION.fullmatch(session_id) is None:
            raise ProtocolFailure(400, "Invalid tester session identifier", "tester")
        if method not in {"GET", "POST"}:
            raise ProtocolFailure(400, "Unsupported project request method", "tester")
        outgoing = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Test-Session-Id": session_id,
        }
        if headers is not None:
            if not isinstance(headers, dict) or any(
                not isinstance(key, str) or key.lower() != "content-type"
                for key in headers
            ):
                raise ProtocolFailure(
                    400, "Only a request Content-Type override is supported", "tester"
                )
            for value in headers.values():
                if not isinstance(value, str) or value not in {
                    "application/json",
                    "text/plain",
                    "application/octet-stream",
                }:
                    raise ProtocolFailure(
                        400, "Unsupported request Content-Type override", "tester"
                    )
                outgoing["Content-Type"] = value
        if content is not None and json is not _UNSET:
            raise ProtocolFailure(400, "Supply JSON or raw content, not both", "tester")
        arguments = {"params": params, "content": content}
        if json is not _UNSET:
            arguments["json"] = json
        transport = (
            _BorrowedTransport(self._transport)
            if self._transport is not None
            else PublicHTTPTransport(allowed_loopback=self._allowed_loopback)
        )
        try:
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(
                    transport=transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=self._timeout,
                    headers=outgoing,
                ) as client:
                    async with _response_stream(
                        client, method, target + path, **arguments
                    ) as response:
                        if 300 <= response.status_code <= 399:
                            raise ProtocolFailure(
                                502, "Target redirects are not permitted", "system"
                            )
                        if (
                            response.headers.get("content-encoding", "identity")
                            .strip()
                            .lower()
                            != "identity"
                        ):
                            raise ProtocolFailure(
                                502,
                                "Target response compression is not supported",
                                "system",
                            )
                        content_bytes = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if len(chunk) > self._max_response_bytes - len(
                                content_bytes
                            ):
                                raise ProtocolFailure(
                                    502,
                                    "Target response exceeds the size limit",
                                    "system",
                                )
                            content_bytes.extend(chunk)
                        # Bytes are decoded by HTTPX already; do not ask the new
                        # bounded response to apply compression a second time.
                        response_headers = [
                            (name, value)
                            for name, value in response.headers.multi_items()
                            if name.lower()
                            not in {
                                "content-encoding",
                                "content-length",
                                "transfer-encoding",
                            }
                        ]
                        return httpx.Response(
                            response.status_code,
                            headers=response_headers,
                            content=bytes(content_bytes),
                            request=response.request,
                        )
        except ProtocolFailure:
            raise
        except _AddressPolicyError:
            raise ProtocolFailure(
                400, "Target resolved to a disallowed network address", "tester"
            ) from None
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise ProtocolFailure(
                504, "Target request timed out", "communication"
            ) from None
        except httpx.DecodingError:
            raise ProtocolFailure(
                502, "Target returned an invalid encoded response", "system"
            ) from None
        except (httpx.HTTPError, OSError):
            raise ProtocolFailure(
                502, "Target request could not complete", "communication"
            ) from None

    async def request(self, target_url, session_id, method, relative_path, **kwargs):
        response = await self._request(
            target_url, session_id, method, relative_path, **kwargs
        )
        if response.status_code >= 500:
            raise ProtocolFailure(
                response.status_code, "Target returned a server error", "system"
            )
        return response

    async def prepare(self, target_url, session_id):
        response = await self._request(
            target_url,
            session_id,
            "POST",
            "/test/setup",
            json={"session_id": session_id, "setup": {}},
        )
        if response.status_code != 201:
            raise _control_failure(response, "Target setup did not confirm readiness")
        try:
            body = SessionResponse.model_validate(_protocol_json(response))
        except (ValueError, TypeError, RecursionError, ValidationError):
            raise ProtocolFailure(
                502, "Target setup returned an invalid session response", "system"
            ) from None
        if body.session_id != session_id:
            raise ProtocolFailure(
                502, "Target setup returned a different session identifier", "system"
            )
        return body.session_id

    async def code_coverage(self, target_url, session_id):
        response = await self._request(
            target_url,
            session_id,
            "GET",
            "/test/report",
            params={"session_id": session_id},
        )
        if response.status_code != 200:
            raise _control_failure(
                response, "Target coverage report could not be retrieved"
            )
        try:
            body = SystemReport.model_validate(_protocol_json(response))
        except (ValueError, TypeError, RecursionError, ValidationError):
            raise ProtocolFailure(
                502, "Target coverage report is invalid", "system"
            ) from None
        if body.session_id != session_id:
            raise ProtocolFailure(
                502, "Target coverage report belongs to another session", "system"
            )
        return body.coverage.code

    async def aclose(self):
        """Live resources are closed per request; injected transports stay owned by their caller."""
