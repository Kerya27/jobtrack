"""SSRF protection: internal addresses are refused before and during the request."""

import socket

import httpx
import pytest

from app.services import ssrf
from app.services.ssrf import (
    MAX_RESPONSE_BYTES,
    FetchError,
    FetchFailure,
    SafeNetworkBackend,
    fetch_html,
    is_public_ip,
    validate_url,
)

PUBLIC_IP = "93.184.215.14"
_real_resolve = ssrf.resolve


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real DNS in tests: *.example names resolve to fixed addresses."""
    table = {
        "jobs.example": [PUBLIC_IP],
        "rebind.example": ["127.0.0.1"],
        "mixed.example": [PUBLIC_IP, "10.0.0.5"],
    }

    def resolve(host: str, port: int) -> list[str]:
        if host in table:
            return table[host]
        return _real_resolve(host, port)  # IP literals and "localhost" resolve offline

    monkeypatch.setattr(ssrf, "resolve", resolve)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8080/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://172.16.5.4/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",  # 127.0.0.1 written as a decimal number
        "http://rebind.example/",
        "http://mixed.example/",  # one private record is enough to refuse
        "ftp://jobs.example/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:pass@jobs.example/",
        "http://jobs.example:22/",
        "https:///nohost",
    ],
)
def test_blocked_urls(url: str) -> None:
    with pytest.raises(FetchError) as exc:
        validate_url(url)
    assert exc.value.reason in {FetchFailure.BAD_URL, FetchFailure.NETWORK}


def test_public_url_is_allowed() -> None:
    assert validate_url("https://jobs.example/rpd/1?x=1").host == "jobs.example"


@pytest.mark.parametrize(
    ("ip", "public"),
    [
        ("8.8.8.8", True),
        ("2a00:1450:4001::200e", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("169.254.169.254", False),
        ("fe80::1%eth0", False),
        ("::ffff:10.0.0.1", False),
        ("224.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(ip: str, public: bool) -> None:
    assert is_public_ip(ip) is public


def test_connect_time_check_blocks_dns_rebinding() -> None:
    """Even if a URL passed validation, the socket never connects to a private IP."""
    with pytest.raises(FetchError):
        SafeNetworkBackend().connect_tcp("rebind.example", 80)
    with pytest.raises(FetchError):
        SafeNetworkBackend().connect_tcp("127.0.0.1", 80)


def test_unresolvable_host_is_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssrf, "resolve", _real_resolve)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(FetchError) as exc:
        validate_url("https://does-not-exist.example/")
    assert exc.value.reason == FetchFailure.NETWORK


# ---------- fetch_html with a mocked transport ----------


def mock(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


def test_fetch_ok_and_charset_detection() -> None:
    body = '<html><head><meta charset="windows-1250"></head><body>Plat ve městě</body></html>'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=body.encode("cp1250")
        )

    page = fetch_html("https://jobs.example/1", transport=mock(handler))
    assert "Plat ve městě" in page.html


def test_redirect_to_internal_address_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "jobs.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
        raise AssertionError("the internal address must never be requested")

    with pytest.raises(FetchError) as exc:
        fetch_html("https://jobs.example/1", transport=mock(handler))
    assert exc.value.reason == FetchFailure.BAD_URL


def test_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": f"/r{len(request.url.path)}"})

    with pytest.raises(FetchError) as exc:
        fetch_html("https://jobs.example/", transport=mock(handler))
    assert exc.value.reason == FetchFailure.NETWORK


def test_relative_redirect_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>ok</title>")

    page = fetch_html("https://jobs.example/old", transport=mock(handler))
    assert page.url == "https://jobs.example/new"


def test_response_size_limit() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"a" * (MAX_RESPONSE_BYTES + 1)
        )

    with pytest.raises(FetchError) as exc:
        fetch_html("https://jobs.example/big", transport=mock(handler))
    assert exc.value.reason == FetchFailure.TOO_BIG


@pytest.mark.parametrize(
    ("status", "content_type", "reason"),
    [
        (999, "text/html", FetchFailure.BLOCKED_BY_SITE),  # LinkedIn
        (403, "text/html", FetchFailure.BLOCKED_BY_SITE),
        (404, "text/html", FetchFailure.NETWORK),
        (200, "application/pdf", FetchFailure.NOT_HTML),
    ],
)
def test_error_responses(status: int, content_type: str, reason: FetchFailure) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": content_type}, content=b"x")

    with pytest.raises(FetchError) as exc:
        fetch_html("https://jobs.example/x", transport=mock(handler))
    assert exc.value.reason == reason


def test_timeout_is_reported_as_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchError) as exc:
        fetch_html("https://jobs.example/slow", transport=mock(handler))
    assert exc.value.reason == FetchFailure.NETWORK
