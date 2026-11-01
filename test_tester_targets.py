"""Target admission is exact and offline; .example hosts are synthetic fixtures."""

import socket

import pytest

from tester_interfaces import ProtocolFailure
from tester_targets import ApprovedTargets


@pytest.mark.parametrize(("configured", "requested", "expected"), [
    ("https://api.example", "https://api.example/", "https://api.example"),
    ("HTTPS://API.EXAMPLE:443/Base/", "https://api.example/Base", "https://api.example/Base"),
    ("http://api.example:80/", "HTTP://API.EXAMPLE", "http://api.example"),
    ("https://api.example:8443/Base/", "https://api.example:8443/Base/", "https://api.example:8443/Base"),
    ("https://api.example./Base", "https://API.EXAMPLE/Base/", "https://api.example/Base"),
    ("https://b\u00fccher.example/Api", "https://xn--bcher-kva.example/Api/", "https://xn--bcher-kva.example/Api"),
])
def test_canonical_full_base_url_matches(configured, requested, expected):
    policy = ApprovedTargets((configured,))
    assert policy.targets == (expected,)
    assert policy.normalize_and_require(requested) == expected


@pytest.mark.parametrize("requested", [
    "https://api.example.evil.example/Base",
    "https://sub.api.example/Base",
    "https://evil-api.example/Base",
    "https://api.example:8443/Base",
    "http://api.example/Base",
    "https://api.example/base",
    "https://api.example/BASE",
    "https://api.example/Base/child",
    "https://api.example/Base-suffix",
    "https://api.example/",
    "https://other.example/Base",
])
def test_approval_requires_whole_origin_and_base_path(requested):
    policy = ApprovedTargets(("https://api.example/Base",))
    with pytest.raises(ProtocolFailure) as caught:
        policy.normalize_and_require(requested)
    assert (caught.value.status_code, caught.value.source) == (400, "tester")
    assert caught.value.message == "Target base URL is not approved"


INVALID_URLS = [
    "", "api.example", "//api.example", "https:///api.example",
    "ftp://api.example/Base", "file:///Base", "javascript:alert(1)",
    "https://user@api.example/Base", "https://user:password@api.example/Base",
    "https://@api.example/Base", "https://api.example@evil.example/Base",
    "https://api.example/Base?", "https://api.example/Base?x=1",
    "https://api.example/Base#", "https://api.example/Base#fragment",
    " https://api.example/Base", "https://api.example/Base ",
    "https://api.\texample/Base", "https://api.example/\nBase",
    "https://api.example/\rBase", "https://api.example/\x00Base",
    "https://api.example/\x7fBase", "https://api.example/\x85Base",
    "https://api.example/\u00a0Base", "https://api.example/\u200bBase",
    "https://api.example/\ud800Base",
    "https://api.example\\evil.example/Base", "https://api.example/Base\\child",
    "https://api.example/%2e%2e/Base", "https://api.example/%252e%252e/Base",
    "https://api.example/Base%2fchild", "https://api.example/Base%3fquery",
    "https://api%2eexample/Base", "https://api.example%40evil.example/Base",
    "https://api.example/a/../Base", "https://api.example/./Base",
    "https://api.example/Base/.", "https://api.example/Base/..",
    "https://api.example//Base", "https://api.example/Base//",
    "https://api.example:/Base", "https://api.example:0/Base",
    "https://api.example:65536/Base", "https://api.example:-1/Base",
    "https://api.example:abc/Base", "https://api.example:+443/Base",
    "https://api.example:443:443/Base",
    "https://api..example/Base", "https://-api.example/Base",
    "https://api-.example/Base", "https://api_example/Base",
    "https://api.example../Base", "https://[api.example]/Base",
    "https://[2001:db8::1]evil.example/Base",
    "https://[2001:db8::1/Base", "https://2001:db8::1/Base",
    "https://localhost/Base", "https://LOCALHOST./Base",
    "https://api.localhost/Base", "https://api.local/Base",
    "https://api.internal/Base", "https://local/Base", "https://internal/Base",
    "https://127.0.0.1/Base", "https://10.0.0.1/Base",
    "https://172.16.0.1/Base", "https://192.168.1.1/Base",
    "https://169.254.169.254/Base", "https://0.0.0.0/Base",
    "https://100.64.0.1/Base", "https://192.0.2.1/Base",
    "https://198.51.100.1/Base", "https://203.0.113.1/Base",
    "https://224.0.0.1/Base", "https://240.0.0.1/Base",
    "https://255.255.255.255/Base", "https://[::]/Base",
    "https://[::1]/Base", "https://[fc00::1]/Base",
    "https://[fe80::1]/Base", "https://[ff02::1]/Base",
    "https://[2001:db8::1]/Base", "https://[fe80::1%25eth0]/Base",
    "https://[::ffff:127.0.0.1]/Base",
    "https://2130706433/Base", "https://127.1/Base",
    "https://0177.0.0.1/Base", "https://0x7f000001/Base",
    "https://0x7f.0.0.1/Base", "https://api.123/Base",
    None, 42,
]


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_trusted_configuration_fails_closed(url):
    with pytest.raises(ValueError):
        ApprovedTargets((url,))


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_request_is_classified_before_admission(url):
    policy = ApprovedTargets(("https://api.example/Base",))
    with pytest.raises(ProtocolFailure) as caught:
        policy.normalize_and_require(url)
    assert (caught.value.status_code, caught.value.source) == (400, "tester")
    assert caught.value.message == "Invalid target base URL"


def test_empty_configuration_permits_no_targets():
    with pytest.raises(ProtocolFailure) as caught:
        ApprovedTargets().normalize_and_require("https://api.example")
    assert (caught.value.status_code, caught.value.source) == (400, "tester")


@pytest.mark.parametrize("targets", [None, "https://api.example", ["https://api.example"]])
def test_configuration_must_be_an_explicit_tuple(targets):
    with pytest.raises(ValueError):
        ApprovedTargets(targets)


def test_multiple_configured_targets_remain_distinct():
    policy = ApprovedTargets(("https://api.example/One", "https://api.example/Two"))
    assert policy.normalize_and_require("https://api.example/One/") == "https://api.example/One"
    assert policy.normalize_and_require("https://api.example/Two") == "https://api.example/Two"
    with pytest.raises(ProtocolFailure):
        policy.normalize_and_require("https://api.example/Three")


def test_admission_does_not_resolve_dns_or_open_connections(monkeypatch):
    def forbidden_network(*args, **kwargs):
        pytest.fail("Logical target admission must not perform network I/O")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_network)
    monkeypatch.setattr(socket, "gethostbyname", forbidden_network)
    monkeypatch.setattr(socket, "create_connection", forbidden_network)
    monkeypatch.setattr(socket, "socket", forbidden_network)
    policy = ApprovedTargets(("https://api.example/Base",))
    assert policy.normalize_and_require("https://API.EXAMPLE:443/Base/") == "https://api.example/Base"
