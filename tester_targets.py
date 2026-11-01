"""Exact logical admission of configured tester base URLs, without network I/O.

This policy does not resolve DNS or establish that an approved DNS name points
to a public address. A future live gateway must enforce DNS/address policy and
redirect restrictions at connection time. No live HTTP transport is supplied.
"""

from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address
import re
from unicodedata import category
from urllib.parse import urlsplit

from tester_interfaces import ProtocolFailure


_AUTHORITY = re.compile(r"(\[[^\[\]]+\]|[^:\[\]]+)(?::([0-9]+))?\Z")
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z")
_LOCAL_NAMES = ("localhost", "local", "internal")


def _canonical_base_url(raw_url: str) -> str:
    """Validate a deliberately narrow HTTP base URL syntax before matching."""
    if not isinstance(raw_url, str) or not raw_url:
        raise ValueError("Target base URL must be a nonempty string")
    if any(char.isspace() or category(char) in {"Cc", "Cf", "Cs"}
           for char in raw_url):
        raise ValueError("Target base URL cannot contain whitespace or controls")
    # Reject encodings altogether: no decoder or downstream URL parser may
    # reinterpret an approved authority, path separator, dot segment or delimiter.
    if any(char in raw_url for char in "\\%?#@"):
        raise ValueError("Target base URL contains a forbidden delimiter or encoding")
    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise ValueError("Invalid target base URL") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Target base URL must use HTTP or HTTPS")
    authority = _AUTHORITY.fullmatch(parsed.netloc)
    if authority is None:
        raise ValueError("Target base URL must have a valid host and optional port")

    raw_host, raw_port = authority.groups()
    bracketed = raw_host.startswith("[")
    if bracketed:
        host = raw_host[1:-1].lower()
    else:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Invalid target hostname") from exc
        if host.endswith("."):
            host = host[:-1]

    try:
        address = ip_address(host)
    except ValueError:
        if bracketed:
            raise ValueError("Only IPv6 addresses may use host brackets") from None
        if (len(host) > 253 or not host
                or any(_DNS_LABEL.fullmatch(label) is None for label in host.split("."))):
            raise ValueError("Invalid target hostname") from None
        # Legacy integer, short, hexadecimal and octal IPv4 spellings must not
        # slip through as DNS names for another parser to reinterpret later.
        if _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1]):
            raise ValueError("Ambiguous numeric target hostname") from None
        if any(host == name or host.endswith("." + name) for name in _LOCAL_NAMES):
            raise ValueError("Local target hostnames are not allowed") from None
    else:
        if bracketed != isinstance(address, IPv6Address):
            raise ValueError("IPv6 target addresses must use brackets")
        if (not address.is_global or address.is_private or address.is_loopback
                or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            raise ValueError("Nonpublic target addresses are not allowed")
        host = f"[{address}]" if bracketed else str(address)

    if raw_port is not None:
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ValueError("Invalid target port") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Invalid target port")
    else:
        port = 80 if parsed.scheme == "http" else 443
    port_suffix = "" if port == {"http": 80, "https": 443}[parsed.scheme] else f":{port}"

    path = parsed.path
    if "//" in path or any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("Target base path cannot contain repeated slashes or dot segments")
    if path.endswith("/"):
        path = path[:-1]
    return f"{parsed.scheme}://{host}{port_suffix}{path}"


@dataclass(frozen=True)
class ApprovedTargets:
    """An explicit full-base-URL allowlist; an empty tuple admits no targets.

    Host names, IDNA spellings, default ports and one final base slash normalize
    before exact comparison. Scheme, nondefault port and base path must match;
    base path case is significant. Configuration errors fail at construction.
    """

    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.targets, tuple):
            raise ValueError("Approved targets must be a tuple of base URLs")
        object.__setattr__(self, "targets", tuple(_canonical_base_url(url) for url in self.targets))

    def normalize_and_require(self, raw_url: str) -> str:
        try:
            normalized = _canonical_base_url(raw_url)
        except ValueError:
            raise ProtocolFailure(400, "Invalid target base URL", "tester") from None
        if normalized not in self.targets:
            raise ProtocolFailure(400, "Target base URL is not approved", "tester")
        return normalized
