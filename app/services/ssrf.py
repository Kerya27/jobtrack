"""Safe fetching of user-supplied URLs (SSRF protection).

The parser downloads pages that *users* point it at, so the server must never be tricked into
calling internal addresses (localhost, the private network, cloud metadata at 169.254.169.254…).

Two layers:
1. ``validate_url`` — scheme/port/host checks and DNS resolution before every request,
   including each redirect hop (redirects are followed manually).
2. ``SafeNetworkBackend`` — the check is repeated at connect time on the exact IP the socket
   connects to, which closes the DNS-rebinding gap (a host that resolves to a public IP during
   the check and to 127.0.0.1 a moment later).
"""

import ipaddress
import logging
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx
from bs4 import UnicodeDammit

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 8.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 JobTrack/1.0"
)
# Status codes job boards use when they refuse bots (LinkedIn answers 999).
BLOCKING_STATUSES = frozenset({401, 403, 429, 451, 999})


class FetchFailure(StrEnum):
    BAD_URL = "bad_url"
    BLOCKED_BY_SITE = "blocked_site"
    NETWORK = "network"
    TOO_BIG = "too_big"
    NOT_HTML = "not_html"


class FetchError(Exception):
    def __init__(self, reason: FetchFailure, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else str(reason))
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FetchedPage:
    url: str  # final URL after redirects
    html: str


def is_public_ip(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])  # drop IPv6 zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is still loopback
    # is_global is False for private, loopback, link-local (incl. 169.254.169.254 metadata),
    # shared/CGNAT, documentation, reserved and unspecified ranges.
    return ip.is_global and not ip.is_multicast


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(FetchFailure.NETWORK, f"cannot resolve {host}") from exc
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    # IPv4 first: many containers (free hosting included) have no IPv6 route at all.
    return sorted(addresses, key=lambda a: ":" in a)


def _ensure_public(host: str, port: int) -> list[str]:
    addresses = resolve(host, port)
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise FetchError(FetchFailure.BAD_URL, f"{host} resolves to a non-public address")
    return addresses


def validate_url(url: str) -> httpx.URL:
    """Raise FetchError(BAD_URL) unless the URL is http(s) to a public host on a web port."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise FetchError(FetchFailure.BAD_URL, "unparsable URL") from exc
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.host:
        raise FetchError(FetchFailure.BAD_URL, "only http(s) URLs with a host are allowed")
    if parsed.userinfo:
        raise FetchError(FetchFailure.BAD_URL, "credentials in URL are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise FetchError(FetchFailure.BAD_URL, f"port {port} is not allowed")
    _ensure_public(parsed.host, port)
    return parsed


class SafeNetworkBackend(httpcore.SyncBackend):
    """Resolves the host itself, refuses non-public IPs, then connects to the checked IP."""

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        last_error: Exception | None = None
        for address in _ensure_public(host, port):
            try:
                # TLS still verifies the certificate against the original host name (SNI).
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError(f"cannot connect to {host}")


class _SafeTransport(httpx.HTTPTransport):
    def __init__(self) -> None:
        super().__init__(retries=0)
        # httpx has no public hook for the network backend, so the pool is rebuilt with ours.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=4,
            network_backend=SafeNetworkBackend(),
        )


def _decode(raw: bytes, charset: str | None) -> str:
    known = [charset] if charset else []
    return UnicodeDammit(raw, known_definite_encodings=known, is_html=True).unicode_markup or ""


def _read_limited(response: httpx.Response, deadline: float) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise FetchError(FetchFailure.TOO_BIG)
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():  # decompressed, so gzip bombs hit the limit too
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise FetchError(FetchFailure.TOO_BIG)
        if time.monotonic() > deadline:
            raise FetchError(FetchFailure.NETWORK, "total timeout")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_html(url: str, transport: httpx.BaseTransport | None = None) -> FetchedPage:
    """Download an HTML page with all the safety limits. Raises FetchError on any problem.

    ``transport`` is only for tests (httpx.MockTransport); production always uses the safe one.
    """
    deadline = time.monotonic() + TIMEOUT_SECONDS
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "cs,en;q=0.9,uk;q=0.8,ru;q=0.7",
    }
    with httpx.Client(
        transport=transport or _SafeTransport(),
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        follow_redirects=False,
        trust_env=False,  # never route through proxies from the environment
        headers=headers,
    ) as client:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            validate_url(current)
            try:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        current = urljoin(str(response.url), location)
                        continue
                    if response.status_code in BLOCKING_STATUSES:
                        raise FetchError(FetchFailure.BLOCKED_BY_SITE, str(response.status_code))
                    if response.status_code >= 400:
                        raise FetchError(FetchFailure.NETWORK, f"HTTP {response.status_code}")
                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not any(
                        kind in content_type for kind in ("text/html", "xhtml", "text/plain")
                    ):
                        raise FetchError(FetchFailure.NOT_HTML, content_type)
                    raw = _read_limited(response, deadline)
                    return FetchedPage(
                        url=str(response.url), html=_decode(raw, response.charset_encoding)
                    )
            except httpx.HTTPError as exc:
                logger.info(
                    "fetch failed for host %s: %s", httpx.URL(current).host, type(exc).__name__
                )
                raise FetchError(FetchFailure.NETWORK, type(exc).__name__) from exc
        raise FetchError(FetchFailure.NETWORK, "too many redirects")
