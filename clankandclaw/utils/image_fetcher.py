import ipaddress
from urllib.parse import urlparse

import httpx

MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def fetch_image_bytes(url: str) -> bytes:
    _validate_image_url(url)

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        _validate_content_type(response.headers.get("content-type"))
        _validate_size(response.headers.get("content-length"), len(response.content))
        return response.content


def _validate_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsafe image URL")

    if _is_unsafe_host(parsed.hostname):
        raise ValueError("unsafe image URL")


def _is_unsafe_host(hostname: str) -> bool:
    normalized = hostname.lower()
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized.endswith(".local")
    ):
        return True

    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _validate_content_type(content_type: str | None) -> None:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if not normalized.startswith("image/"):
        raise ValueError("response must provide an image content type")


def _validate_size(content_length: str | None, actual_size: int) -> None:
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > MAX_IMAGE_BYTES:
            raise ValueError("image response is too large")

    if actual_size > MAX_IMAGE_BYTES:
        raise ValueError("image response is too large")
