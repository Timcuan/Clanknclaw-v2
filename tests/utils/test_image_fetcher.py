import socket

import httpx
import pytest

from clankandclaw.utils.image_fetcher import MAX_IMAGE_BYTES, fetch_image_bytes


class _DummyStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        await self.response.aclose()
        return False


class _DummyResponse:
    def __init__(
        self,
        content: bytes,
        headers: dict[str, str] | None = None,
        *,
        request_url: str = "https://example.com/image.png",
        status_code: int = 200,
        chunks: list[bytes] | None = None,
    ):
        self._content = content
        self._chunks = chunks
        self.headers = headers or {}
        self.request = httpx.Request("GET", request_url)
        self.status_code = status_code
        self.raise_for_status_called = False
        self.content_accessed = False
        self.chunks_yielded = 0
        self.closed = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    @property
    def content(self) -> bytes:
        self.content_accessed = True
        return self._content

    @property
    def is_redirect(self) -> bool:
        return self.status_code in {301, 302, 303, 307, 308} and "location" in self.headers

    async def aiter_bytes(self):
        chunks = self._chunks or [self._content]
        for chunk in chunks:
            self.chunks_yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _DummyClient:
    responses_by_url = {
        "https://example.com/image.png": _DummyResponse(
            b"image-bytes",
            headers={"content-type": "image/png", "content-length": "11"},
        )
    }

    def __init__(self, *args, **kwargs):
        self.requested_url = None
        self.sent_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.requested_url = url
        current_url = url
        redirects = 0
        while True:
            response = type(self).responses_by_url[current_url]
            response.request = httpx.Request("GET", current_url)
            if not response.is_redirect:
                return response

            redirects += 1
            if redirects > 20:
                raise RuntimeError("too many redirects in test double")
            current_url = str(response.request.url.join(response.headers["location"]))

    def build_request(self, method: str, url: str):
        return httpx.Request(method, url)

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        follow_redirects: bool = False,
    ):
        self.sent_urls.append(str(request.url))
        response = type(self).responses_by_url[str(request.url)]
        response.request = request
        return response

    def stream(self, method: str, url: str, *, follow_redirects: bool = False):
        request = self.build_request(method, url)
        response = type(self).responses_by_url[str(request.url)]
        response.request = request
        return _DummyStream(response)


def _getaddrinfo_stub(
    hostname_to_ip: dict[str, str],
):
    def _stub(host: str, port: int, *args, **kwargs):
        resolved_ip = hostname_to_ip.get(host, "93.184.216.34")
        if ":" in resolved_ip:
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (resolved_ip, port, 0, 0),
                )
            ]

        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (resolved_ip, port),
            )
        ]

    return _stub


@pytest.fixture(autouse=True)
def _stub_safe_dns_resolution(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_stub({}))


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_response_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.responses_by_url = {
        "https://example.com/image.png": _DummyResponse(
            b"image-bytes",
            headers={"content-type": "image/png", "content-length": "11"},
        )
    }

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
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _getaddrinfo_stub({"internal.example": "10.0.0.5"}),
    )
    _DummyClient.responses_by_url = {
        "https://internal.example/image.png": _DummyResponse(
            b"image-bytes",
            headers={"content-type": "image/png", "content-length": "11"},
            request_url="https://internal.example/image.png",
        )
    }

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://internal.example/image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_non_image_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.responses_by_url = {
        "https://example.com/not-image": _DummyResponse(
            b"not-an-image",
            headers={"content-type": "text/html", "content-length": "12"},
            request_url="https://example.com/not-image",
        )
    }

    with pytest.raises(ValueError, match="image content type"):
        await fetch_image_bytes("https://example.com/not-image")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.responses_by_url = {
        "https://example.com/huge-image.png": _DummyResponse(
            b"x",
            headers={
                "content-type": "image/png",
                "content-length": str(MAX_IMAGE_BYTES + 1),
            },
            request_url="https://example.com/huge-image.png",
        )
    }

    with pytest.raises(ValueError, match="too large"):
        await fetch_image_bytes("https://example.com/huge-image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_chain_to_unsafe_host(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.responses_by_url = {
        "https://example.com/start.png": _DummyResponse(
            b"",
            headers={"location": "http://127.0.0.1/private.png"},
            request_url="https://example.com/start.png",
            status_code=302,
        ),
        "http://127.0.0.1/private.png": _DummyResponse(
            b"image-bytes",
            headers={"content-type": "image/png", "content-length": "11"},
            request_url="http://127.0.0.1/private.png",
        ),
    }

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://example.com/start.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_redirect_hostname_resolving_to_unsafe_address(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
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
    _DummyClient.responses_by_url = {
        "https://example.com/start.png": _DummyResponse(
            b"",
            headers={"location": "https://redirect.internal.example/private.png"},
            request_url="https://example.com/start.png",
            status_code=302,
        ),
        "https://redirect.internal.example/private.png": _DummyResponse(
            b"image-bytes",
            headers={"content-type": "image/png", "content-length": "11"},
            request_url="https://redirect.internal.example/private.png",
        ),
    }

    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("https://example.com/start.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_streaming_payload_without_trustworthy_length(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    response = _DummyResponse(
        b"x" * (MAX_IMAGE_BYTES + 2),
        headers={"content-type": "image/png", "content-length": "invalid"},
        request_url="https://example.com/streamed-image.png",
        chunks=[b"x" * MAX_IMAGE_BYTES, b"y", b"z"],
    )
    _DummyClient.responses_by_url = {
        "https://example.com/streamed-image.png": response,
    }

    with pytest.raises(ValueError, match="too large"):
        await fetch_image_bytes("https://example.com/streamed-image.png")

    assert response.content_accessed is False
    assert response.chunks_yielded == 2
