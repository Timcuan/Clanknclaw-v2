import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def fetch_image_bytes(url: str) -> bytes:
    await _validate_fetch_target(url)

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        request = client.build_request("GET", url)
        redirects_followed = 0
        max_redirects = getattr(client, "max_redirects", 20)

        while True:
            response = await client.send(
                request,
                stream=True,
                follow_redirects=False,
            )
            try:
                if response.is_redirect and "location" in response.headers:
                    redirects_followed += 1
                    if redirects_followed > max_redirects:
                        raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.")

                    redirect_url = str(request.url.join(response.headers["location"]))
                    await _validate_fetch_target(redirect_url)
                    request = client.build_request("GET", redirect_url)
                    continue

                response.raise_for_status()
                _validate_content_type(response.headers.get("content-type"))
                _validate_size(response.headers.get("content-length"))
                return await _read_limited_body(response)
            finally:
                await response.aclose()


def _validate_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsafe image URL")

    if _is_unsafe_host(parsed.hostname):
        raise ValueError("unsafe image URL")


async def _validate_fetch_target(url: str) -> None:
    _validate_image_url(url)
    await _validate_resolved_hostname(url)


async def _validate_resolved_hostname(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("unsafe image URL")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolved_records = await asyncio.to_thread(
        socket.getaddrinfo,
        hostname,
        port,
        0,
        socket.SOCK_STREAM,
    )

    for family, _socktype, _proto, _canonname, sockaddr in resolved_records:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue

        if _is_unsafe_ip_address(sockaddr[0]):
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

    return _is_unsafe_ip(address)


def _is_unsafe_ip_address(address_text: str) -> bool:
    normalized = address_text.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return _is_unsafe_ip(address)


def _is_unsafe_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
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


def _validate_size(content_length: str | None) -> None:
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > MAX_IMAGE_BYTES:
            raise ValueError("image response is too large")


async def _read_limited_body(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total_size = 0

    async for chunk in response.aiter_bytes():
        total_size += len(chunk)
        if total_size > MAX_IMAGE_BYTES:
            raise ValueError("image response is too large")
        chunks.append(chunk)

    return b"".join(chunks)
