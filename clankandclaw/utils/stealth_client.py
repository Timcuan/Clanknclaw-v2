"""Stealth HTTP client — wraps httpx.AsyncClient with UA rotation and Gaussian jitter."""

import asyncio
import random
from typing import Any

import httpx

from clankandclaw.config import StealthConfig
from clankandclaw.utils.ua_profiles import UAProfile, pick_profile

_FALLBACK_UA = "ClankAndClaw/1.0 (+ops)"
_FALLBACK_HEADERS: dict[str, str] = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
}


class StealthClient:
    """httpx.AsyncClient wrapper with browser-realistic UA rotation and Gaussian jitter."""

    def __init__(self, config: StealthConfig, timeout: float = 20.0) -> None:
        self._config = config
        self._request_count = 0
        self._profile: UAProfile | None = pick_profile() if config.enabled else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        )

    @property
    def current_ua(self) -> str:
        if not self._config.enabled or self._profile is None:
            return _FALLBACK_UA
        return self._profile.ua

    def _merged_headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        if not self._config.enabled or self._profile is None:
            base: dict[str, str] = {"user-agent": _FALLBACK_UA, **_FALLBACK_HEADERS}
        else:
            base = {"user-agent": self._profile.ua, **self._profile.headers}
        if extra:
            base.update(extra)
        return base

    def _maybe_rotate(self) -> None:
        if not self._config.enabled or self._profile is None:
            return
        self._request_count += 1
        if self._request_count >= self._config.rotate_every:
            self._profile = pick_profile(exclude_ua=self._profile.ua)
            self._request_count = 0

    def on_response(self, status_code: int) -> None:
        """Call after each response. Triggers forced UA rotation on 403/429."""
        if not self._config.enabled or self._profile is None:
            return
        if status_code in {403, 429}:
            self._profile = pick_profile(exclude_ua=self._profile.ua)
            self._request_count = 0

    async def sleep_jitter(self, base_seconds: float) -> None:
        """Sleep for base_seconds with Gaussian variance. Clamps to configured bounds."""
        if not self._config.enabled:
            await asyncio.sleep(random.uniform(0.0, 0.2))
            return
        sigma = base_seconds * self._config.jitter_sigma_pct
        delay = random.gauss(mu=base_seconds, sigma=sigma)
        min_s = self._config.jitter_min_ms / 1000.0
        max_s = self._config.jitter_max_ms / 1000.0
        await asyncio.sleep(max(min_s, min(max_s, delay)))

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        self._maybe_rotate()
        kw: dict[str, Any] = {"headers": self._merged_headers(headers)}
        if params is not None:
            kw["params"] = params
        if timeout is not None:
            kw["timeout"] = httpx.Timeout(timeout)
        return await self._client.get(url, **kw)

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        self._maybe_rotate()
        kw: dict[str, Any] = {"headers": self._merged_headers(headers)}
        if data is not None:
            kw["data"] = data
        if json is not None:
            kw["json"] = json
        if timeout is not None:
            kw["timeout"] = httpx.Timeout(timeout)
        return await self._client.post(url, **kw)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "StealthClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
