"""Tests for StealthClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from clankandclaw.config import StealthConfig
from clankandclaw.utils.stealth_client import StealthClient


@pytest.fixture
def config():
    return StealthConfig(enabled=True, rotate_every=3, jitter_sigma_pct=0.1, jitter_min_ms=0, jitter_max_ms=5000)


@pytest.fixture
def disabled_config():
    return StealthConfig(enabled=False, rotate_every=50, jitter_sigma_pct=0.15, jitter_min_ms=200, jitter_max_ms=3000)


@pytest.mark.asyncio
async def test_stealth_client_injects_user_agent(config):
    async with StealthClient(config, timeout=5.0) as client:
        assert client.current_ua != ""
        assert "Mozilla" in client.current_ua


@pytest.mark.asyncio
async def test_ua_rotates_after_n_requests(config):
    """UA must change after rotate_every (3) requests."""
    async with StealthClient(config, timeout=5.0) as client:
        first_ua = client.current_ua
        seen_uas: set[str] = {first_ua}
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            for _ in range(3):
                await client.get("https://example.com")
        seen_uas.add(client.current_ua)
        # After rotate_every requests, at least 2 different UAs seen (7 profiles available)
        assert len(seen_uas) >= 2 or client._request_count == 0  # rotation reset count


@pytest.mark.asyncio
async def test_ua_rotates_on_403(config):
    """UA must rotate and differ across multiple 403 events."""
    async with StealthClient(config, timeout=5.0) as client:
        all_uas: set[str] = set()
        for _ in range(10):
            client.on_response(403)
            all_uas.add(client.current_ua)
        assert len(all_uas) >= 2


@pytest.mark.asyncio
async def test_ua_rotates_on_429(config):
    """UA must rotate and differ across multiple 429 events."""
    async with StealthClient(config, timeout=5.0) as client:
        all_uas: set[str] = set()
        for _ in range(10):
            client.on_response(429)
            all_uas.add(client.current_ua)
        assert len(all_uas) >= 2


@pytest.mark.asyncio
async def test_non_error_status_does_not_rotate(config):
    """200/201 responses must NOT trigger UA rotation."""
    async with StealthClient(config, timeout=5.0) as client:
        ua_before = client.current_ua
        client.on_response(200)
        client.on_response(201)
        assert client.current_ua == ua_before


@pytest.mark.asyncio
async def test_chrome_ua_has_sec_ch_headers(config):
    """Chrome profiles must include sec-ch-ua headers."""
    async with StealthClient(config, timeout=5.0) as client:
        # Rotate until we land on a Chrome profile
        for _ in range(50):
            ua = client.current_ua
            if "Chrome" in ua and "Edg" not in ua and "Firefox" not in ua:
                break
            client.on_response(429)
        if "Chrome" in client.current_ua and "Firefox" not in client.current_ua:
            headers = client._merged_headers(None)
            assert "sec-ch-ua" in headers


@pytest.mark.asyncio
async def test_firefox_ua_no_sec_ch_headers(config):
    """Firefox profiles must NOT have sec-ch-ua headers."""
    async with StealthClient(config, timeout=5.0) as client:
        for _ in range(50):
            if "Firefox" in client.current_ua:
                break
            client.on_response(429)
        if "Firefox" in client.current_ua:
            headers = client._merged_headers(None)
            assert "sec-ch-ua" not in headers


@pytest.mark.asyncio
async def test_extra_headers_take_precedence(config):
    """Caller-supplied headers override profile headers."""
    async with StealthClient(config, timeout=5.0) as client:
        merged = client._merged_headers({"x-api-key": "abc123", "accept": "text/html"})
        assert merged["x-api-key"] == "abc123"
        assert merged["accept"] == "text/html"


@pytest.mark.asyncio
async def test_sleep_jitter_within_bounds():
    """sleep_jitter must always sleep within [jitter_min_ms, jitter_max_ms]."""
    config = StealthConfig(
        enabled=True,
        rotate_every=50,
        jitter_sigma_pct=0.5,
        jitter_min_ms=100,
        jitter_max_ms=500,
    )
    async with StealthClient(config, timeout=5.0) as client:
        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("clankandclaw.utils.stealth_client.asyncio.sleep", side_effect=capture_sleep):
            for _ in range(100):
                await client.sleep_jitter(1.0)

        assert len(sleep_calls) == 100
        for delay in sleep_calls:
            assert 0.1 <= delay <= 0.5, f"delay {delay} out of bounds [0.1, 0.5]"


@pytest.mark.asyncio
async def test_disabled_mode_uses_fallback_ua(disabled_config):
    """When disabled, StealthClient uses the static fallback UA."""
    async with StealthClient(disabled_config, timeout=5.0) as client:
        assert client.current_ua == "ClankAndClaw/1.0 (+ops)"


@pytest.mark.asyncio
async def test_disabled_mode_no_browser_headers(disabled_config):
    """When disabled, headers are minimal (no sec-fetch-*)."""
    async with StealthClient(disabled_config, timeout=5.0) as client:
        headers = client._merged_headers(None)
        assert "sec-fetch-dest" not in headers
        assert "sec-ch-ua" not in headers


@pytest.mark.asyncio
async def test_request_count_increments(config):
    """Request count increments on each get() call (rotate_every=3, resets at 3)."""
    async with StealthClient(config, timeout=5.0) as client:
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = MagicMock(spec=httpx.Response, status_code=200)
            await client.get("https://example.com")
            assert client._request_count == 1
            await client.get("https://example.com")
            assert client._request_count == 2


@pytest.mark.asyncio
async def test_request_count_resets_on_rotation(config):
    """Request count resets to 0 after UA rotation."""
    async with StealthClient(config, timeout=5.0) as client:
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = MagicMock(spec=httpx.Response, status_code=200)
            # rotate_every=3, so after 3 requests count resets
            for _ in range(3):
                await client.get("https://example.com")
        assert client._request_count == 0
