import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clankandclaw.utils.image_fetcher import MAX_IMAGE_BYTES, fetch_image_bytes
from clankandclaw.utils.stealth_client import StealthClient


def _getaddrinfo_stub(hostname_to_ip: dict[str, str]):
    def _stub(host: str, port: int, *args, **kwargs):
        resolved_ip = hostname_to_ip.get(host, "93.184.216.34")
        if ":" in resolved_ip:
            return [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (resolved_ip, port, 0, 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (resolved_ip, port))]
    return _stub


def _mock_response(
    status_code: int = 200,
    content_type: str = "image/png",
    body: bytes = b"image-bytes",
    content_length: str | None = None,
    location: str | None = None,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    headers: dict[str, str] = {}
    if content_type:
        headers["content-type"] = content_type
    if content_length is not None:
        headers["content-length"] = content_length
    if location is not None:
        headers["location"] = location
    resp.headers = headers
    resp.content = body
    return resp


@pytest.fixture(autouse=True)
def _stub_safe_dns_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_stub({}))


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_image_body():
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(body=b"image-bytes")
        result = await fetch_image_bytes("https://example.com/image.png")
    assert result == b"image-bytes"


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_unsafe_url():
    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("http://127.0.0.1/image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_hostname_resolving_to_unsafe_address(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_stub({"internal.example": "10.0.0.5"}))
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        with pytest.raises(ValueError, match="unsafe image URL"):
            await fetch_image_bytes("https://internal.example/image.png")
    mock_get.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_non_image_content_type():
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(content_type="text/html", body=b"not-an-image")
        with pytest.raises(ValueError, match="image content type"):
            await fetch_image_bytes("https://example.com/not-image")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_declared_content_length():
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(
            content_length=str(MAX_IMAGE_BYTES + 1),
            body=b"x",
        )
        with pytest.raises(ValueError, match="too large"):
            await fetch_image_bytes("https://example.com/huge-image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_body():
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(body=b"x" * (MAX_IMAGE_BYTES + 1))
        with pytest.raises(ValueError, match="too large"):
            await fetch_image_bytes("https://example.com/huge-image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_chain_to_unsafe_host(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_stub({"example.com": "93.184.216.34"}))
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(
            status_code=302,
            content_type="",
            body=b"",
            location="http://127.0.0.1/private.png",
        )
        with pytest.raises(ValueError, match="unsafe image URL"):
            await fetch_image_bytes("https://example.com/start.png")
    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_hostname_resolving_to_unsafe_address(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub(
            {
                "example.com": "93.184.216.34",
                "redirect.internal.example": "169.254.169.254",
            }
        ),
    )
    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(
            status_code=302,
            content_type="",
            body=b"",
            location="https://redirect.internal.example/private.png",
        )
        with pytest.raises(ValueError, match="unsafe image URL"):
            await fetch_image_bytes("https://example.com/start.png")
    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_image_bytes_follows_redirect_to_safe_host(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub(
            {
                "example.com": "93.184.216.34",
                "cdn.example.net": "151.101.1.69",
            }
        ),
    )
    redirect_response = _mock_response(
        status_code=302, content_type="", body=b"", location="https://cdn.example.net/final.png"
    )
    final_response = _mock_response(body=b"redirected-image")

    with patch.object(StealthClient, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [redirect_response, final_response]
        result = await fetch_image_bytes("https://example.com/start.png")

    assert result == b"redirected-image"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].args[0] == "https://example.com/start.png"
    assert mock_get.call_args_list[1].args[0] == "https://cdn.example.net/final.png"


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_localhost_hostname():
    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("http://localhost/image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_scheme_other_than_http():
    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("ftp://example.com/image.png")
