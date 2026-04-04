import httpx
import pytest

from clankandclaw.utils.image_fetcher import MAX_IMAGE_BYTES, fetch_image_bytes


class _DummyResponse:
    def __init__(self, content: bytes, headers: dict[str, str] | None = None):
        self.content = content
        self.headers = headers or {}
        self.raise_for_status_called = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True


class _DummyClient:
    response = _DummyResponse(
        b"image-bytes",
        headers={"content-type": "image/png", "content-length": "11"},
    )

    def __init__(self, *args, **kwargs):
        self.requested_url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str):
        self.requested_url = url
        return self.response


@pytest.mark.asyncio
async def test_fetch_image_bytes_returns_response_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse(
        b"image-bytes",
        headers={"content-type": "image/png", "content-length": "11"},
    )

    result = await fetch_image_bytes("https://example.com/image.png")

    assert result == b"image-bytes"


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_unsafe_url():
    with pytest.raises(ValueError, match="unsafe image URL"):
        await fetch_image_bytes("http://127.0.0.1/image.png")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_non_image_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse(
        b"not-an-image",
        headers={"content-type": "text/html", "content-length": "12"},
    )

    with pytest.raises(ValueError, match="image content type"):
        await fetch_image_bytes("https://example.com/not-image")


@pytest.mark.asyncio
async def test_fetch_image_bytes_rejects_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse(
        b"x",
        headers={
            "content-type": "image/png",
            "content-length": str(MAX_IMAGE_BYTES + 1),
        },
    )

    with pytest.raises(ValueError, match="too large"):
        await fetch_image_bytes("https://example.com/huge-image.png")
