import httpx


async def fetch_image_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=8.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content
