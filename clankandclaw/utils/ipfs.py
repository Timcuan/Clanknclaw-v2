import os
from collections.abc import Mapping
from typing import Any

import httpx


class PinataClient:
    base_url = "https://api.pinata.cloud/pinning"

    def __init__(self, jwt: str | None = None):
        self.jwt = jwt or os.getenv("PINATA_JWT")
        if not self.jwt:
            raise ValueError("PINATA_JWT is required")

    async def upload_file_bytes(
        self,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/pinFileToIPFS",
                headers=self._headers(),
                files={"file": (filename, content, content_type)},
            )
            response.raise_for_status()
            payload = response.json()
            return payload["IpfsHash"]

    async def upload_json_metadata(self, metadata: Mapping[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/pinJSONToIPFS",
                headers=self._headers(),
                json=dict(metadata),
            )
            response.raise_for_status()
            payload = response.json()
            return payload["IpfsHash"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.jwt}"}
