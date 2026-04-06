import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from clankandclaw.config import StealthConfig
from clankandclaw.utils.stealth_client import StealthClient

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 20
REQUEST_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class _ResolvedFetchTarget:
    url: str
    scheme: str
    hostname: str
    port: int
    ip_text: str
    request_target: str
    host_header: str


async def fetch_image_bytes(url: str, stealth: StealthClient | None = None) -> bytes:
    """Fetch image bytes from url. Validates against SSRF before each request/redirect.

    If stealth is None, creates a temporary StealthClient with default config.
    """
    _own_stealth = stealth is None
    if _own_stealth:
        stealth = StealthClient(StealthConfig(), timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        # Standard Browser User-Agent to bypass generic bot filters
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        return await _fetch_with_stealth(url, stealth, user_agent=user_agent)
    finally:
        if _own_stealth:
            await stealth.aclose()


async def _fetch_with_stealth(url: str, stealth: StealthClient, user_agent: str) -> bytes:
    current_url = url
    redirects_followed = 0
    max_retries = 3
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            # SSRF validation before every request
            await _resolve_fetch_target(current_url)

            response = await stealth.get(
                current_url,
                headers={
                    "accept": "image/*, */*",
                    "user-agent": user_agent,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if _is_redirect(response.status_code):
                redirect_location = response.headers.get("location")
                if not redirect_location:
                    break
                redirects_followed += 1
                if redirects_followed > MAX_REDIRECTS:
                    raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.")
                current_url = urljoin(current_url, redirect_location)
                continue

            httpx.Response(
                response.status_code,
                headers=dict(response.headers),
                request=httpx.Request("GET", current_url),
            ).raise_for_status()

            _validate_content_type(response.headers.get("content-type"))
            _validate_size(response.headers.get("content-length"))

            body = response.content
            if len(body) > MAX_IMAGE_BYTES:
                raise ValueError("image response is too large")
            return body

        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            logger.warning(f"Image fetch attempt {attempt+1} failed: {exc}, retrying...")
            await asyncio.sleep(retry_delay * (attempt + 1))

    raise ValueError("redirect loop or max retries without valid response")


def _validate_image_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsafe image URL")
    if _is_unsafe_host(parsed.hostname):
        raise ValueError("unsafe image URL")


async def _resolve_fetch_target(url: str) -> _ResolvedFetchTarget:
    _validate_image_url(url)
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

    chosen_ip: str | None = None
    for family, _socktype, _proto, _canonname, sockaddr in resolved_records:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        resolved_ip = sockaddr[0]
        if _is_unsafe_ip_address(resolved_ip):
            raise ValueError("unsafe image URL")
        if chosen_ip is None:
            chosen_ip = resolved_ip

    if chosen_ip is None:
        raise ValueError("unsafe image URL")

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return _ResolvedFetchTarget(
        url=url,
        scheme=parsed.scheme,
        hostname=hostname,
        port=port,
        ip_text=chosen_ip,
        request_target=path,
        host_header=_format_host_header(hostname, port, parsed.scheme),
    )


def _format_host_header(hostname: str, port: int, scheme: str) -> str:
    is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    host_value = hostname
    if ":" in hostname and not hostname.startswith("["):
        host_value = f"[{hostname}]"
    if is_default_port:
        return host_value
    return f"{host_value}:{port}"


def _is_redirect(status_code: int) -> bool:
    return status_code in {301, 302, 303, 307, 308}


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
    return any((
        address.is_private,
        address.is_loopback,
        address.is_link_local,
        address.is_multicast,
        address.is_reserved,
        address.is_unspecified,
    ))


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
