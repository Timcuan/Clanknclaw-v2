import os
import json
import hashlib
import mimetypes
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx


class PinataClient:
    base_url = "https://api.pinata.cloud/pinning"

    def __init__(self, jwt: str | None = None, cache_path: str | None = None):
        self.jwt = jwt or os.getenv("PINATA_JWT")
        if not self.jwt:
            raise ValueError("PINATA_JWT is required")
        cache_default = Path(os.getenv("PINATA_CACHE_PATH", ".pinata_cache.json"))
        self.cache_path = Path(cache_path) if cache_path else cache_default
        self._cache = self._load_cache()
        self._cache_max_entries = int(os.getenv("PINATA_CACHE_MAX_ENTRIES", "20000"))
        self._cache_flush_every = int(os.getenv("PINATA_CACHE_FLUSH_EVERY", "25"))
        self._dirty_updates = 0

    def _load_cache(self) -> dict[str, str]:
        if not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text())
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, sort_keys=True))
        self._dirty_updates = 0

    def _evict_if_needed(self) -> None:
        overflow = len(self._cache) - self._cache_max_entries
        if overflow <= 0:
            return
        # Dict preserves insertion order in modern Python.
        for key in list(self._cache.keys())[:overflow]:
            self._cache.pop(key, None)

    def _schedule_cache_flush(self) -> None:
        self._dirty_updates += 1
        must_flush = (
            self._dirty_updates >= self._cache_flush_every
            or not self.cache_path.exists()
        )
        if must_flush:
            self._save_cache()

    def _cache_key(self, payload: bytes, *, kind: str) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        return f"{kind}:{digest}"

    def _cache_get(self, payload: bytes, *, kind: str) -> str | None:
        return self._cache.get(self._cache_key(payload, kind=kind))

    def _cache_set(self, payload: bytes, cid: str, *, kind: str) -> None:
        self._cache[self._cache_key(payload, kind=kind)] = cid
        self._evict_if_needed()
        self._schedule_cache_flush()

    def normalize_cid(self, raw_cid: str) -> str:
        cid = raw_cid.strip()
        if cid.startswith("ipfs://"):
            cid = cid.removeprefix("ipfs://")
        if not cid:
            raise ValueError("invalid CID from Pinata")
        return cid

    async def upload_file_bytes(
        self,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        cached = self._cache_get(content, kind="file")
        if cached:
            return cached

        guessed_type, _ = mimetypes.guess_type(filename)
        final_content_type = content_type or guessed_type or "application/octet-stream"

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/pinFileToIPFS",
                headers=self._headers(),
                files={"file": (filename, content, final_content_type)},
            )
            response.raise_for_status()
            payload = response.json()
            cid = self.normalize_cid(payload["IpfsHash"])
            self._cache_set(content, cid, kind="file")
            return cid

    async def upload_json_metadata(self, metadata: Mapping[str, Any]) -> str:
        canonical_json = json.dumps(dict(metadata), sort_keys=True, separators=(",", ":")).encode("utf-8")
        cached = self._cache_get(canonical_json, kind="json")
        if cached:
            return cached

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/pinJSONToIPFS",
                headers=self._headers(),
                json=dict(metadata),
            )
            response.raise_for_status()
            payload = response.json()
            cid = self.normalize_cid(payload["IpfsHash"])
            self._cache_set(canonical_json, cid, kind="json")
            return cid

    async def upload_any(
        self,
        filename: str,
        content: bytes,
        content_type: str | None = None,
    ) -> str:
        """Upload any binary payload to Pinata with best-effort MIME detection."""
        return await self.upload_file_bytes(filename=filename, content=content, content_type=content_type)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt}"}
