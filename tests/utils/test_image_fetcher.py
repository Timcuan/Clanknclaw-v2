import httpx
import pytest

from clankandclaw.utils.image_fetcher import fetch_image_bytes


class _DummyResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.raise_for_status_called = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True


class _DummyClient:
    def __init__(self, *args, **kwargs):
        self.requested_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.requested_url = url
        return _DummyResponse(b"image-bytes")


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_response_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)

    result = await fetch_image_bytes("https://example.com/image.png")

    assert result == b"image-bytes"
