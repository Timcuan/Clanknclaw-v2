import httpx
import pytest

from clankandclaw.utils.ipfs import PinataClient


def test_pinata_client_reads_jwt_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PINATA_JWT", "token-from-env")

    client = PinataClient()

    assert client.jwt == "token-from-env"


def test_pinata_client_requires_jwt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)

    with pytest.raises(ValueError, match="PINATA_JWT is required"):
        PinataClient()


class _DummyResponse:
    def __init__(self, payload: dict[str, str]):
        self.payload = payload
        self.raise_for_status_called = False

    def raise_for_status(self) -> None:
        self.raise_for_status_called = True

    def json(self) -> dict[str, str]:
        return self.payload


class _DummyClient:
    response = _DummyResponse({"IpfsHash": "QmTest"})
    calls: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        type(self).calls = []
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        type(self).calls.append({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_pinata_client_upload_file_bytes_posts_multipart_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse({"IpfsHash": "QmFile"})

    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))

    ipfs_hash = await client.upload_file_bytes(
        filename="image.png",
        content=b"png-bytes",
        content_type="image/png",
    )

    assert ipfs_hash == "QmFile"
    assert _DummyClient.calls == [
        {
            "url": "https://api.pinata.cloud/pinning/pinFileToIPFS",
            "headers": {"Authorization": "Bearer pinata-jwt"},
            "files": {"file": ("image.png", b"png-bytes", "image/png")},
        }
    ]


@pytest.mark.asyncio
async def test_pinata_client_upload_json_metadata_posts_json_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse({"IpfsHash": "QmJson"})

    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))

    ipfs_hash = await client.upload_json_metadata({"name": "Pepe", "symbol": "PEPE"})

    assert ipfs_hash == "QmJson"
    assert _DummyClient.calls == [
        {
            "url": "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            "headers": {"Authorization": "Bearer pinata-jwt"},
            "json": {"name": "Pepe", "symbol": "PEPE"},
        }
    ]


@pytest.mark.asyncio
async def test_pinata_client_normalizes_ipfs_prefix_and_caches_json(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse({"IpfsHash": "ipfs://QmJson"})

    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))
    first = await client.upload_json_metadata({"name": "Pepe"})
    second = await client.upload_json_metadata({"name": "Pepe"})

    assert first == "QmJson"
    assert second == "QmJson"
    # second call should be cache hit; only one network post
    assert len(_DummyClient.calls) == 1


@pytest.mark.asyncio
async def test_pinata_client_upload_any_uses_mime_guess(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)
    _DummyClient.response = _DummyResponse({"IpfsHash": "QmAny"})

    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))
    ipfs_hash = await client.upload_any("logo.webp", b"webp-bytes")

    assert ipfs_hash == "QmAny"
    assert _DummyClient.calls[0]["files"]["file"][2] == "image/webp"


def test_pinata_client_evicts_old_cache_entries(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("PINATA_CACHE_MAX_ENTRIES", "2")
    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))
    client._cache = {}
    client._cache_set(b"a", "QmA", kind="file")
    client._cache_set(b"b", "QmB", kind="file")
    client._cache_set(b"c", "QmC", kind="file")
    assert len(client._cache) == 2
    assert client._cache_get(b"a", kind="file") is None
    assert client._cache_get(b"b", kind="file") == "QmB"
    assert client._cache_get(b"c", kind="file") == "QmC"


def test_pinata_client_flushes_cache_in_batches(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("PINATA_CACHE_FLUSH_EVERY", "3")
    client = PinataClient(jwt="pinata-jwt", cache_path=str(tmp_path / "pinata-cache.json"))
    save_calls = {"count": 0}

    def fake_save():
        save_calls["count"] += 1
        client.cache_path.parent.mkdir(parents=True, exist_ok=True)
        client.cache_path.write_text("{}")
        client._dirty_updates = 0

    monkeypatch.setattr(client, "_save_cache", fake_save)
    client._cache = {}
    client._cache_set(b"a", "QmA", kind="file")
    client._cache_set(b"b", "QmB", kind="file")
    assert save_calls["count"] == 1  # first write flushes because cache file does not exist
    client._cache_set(b"c", "QmC", kind="file")
    client._cache_set(b"d", "QmD", kind="file")
    client._cache_set(b"e", "QmE", kind="file")
    assert save_calls["count"] == 2
