# StealthClient Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc HTTP headers and jitter in all detectors with a centralized `StealthClient` that rotates browser-realistic UA profiles, injects full header sets, and applies Gaussian jitter.

**Architecture:** New `clankandclaw/utils/ua_profiles.py` holds a weighted pool of 7 browser UA+header profiles. `clankandclaw/utils/stealth_client.py` wraps `httpx.AsyncClient`, injecting profile headers per request, rotating UA on schedule or on 403/429. Farcaster/Gecko workers swap their `httpx.AsyncClient` for `StealthClient`; `image_fetcher.py` replaces its raw socket with `StealthClient.get()` while keeping SSRF IP validation.

**Tech Stack:** Python 3.11+, httpx, asyncio, pydantic (already in use)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `clankandclaw/utils/ua_profiles.py` | **Create** | UA + header profile database, weighted picker |
| `clankandclaw/utils/stealth_client.py` | **Create** | `StealthClient` wrapping httpx, UA rotation, Gaussian jitter |
| `tests/utils/test_stealth_client.py` | **Create** | Unit tests for StealthClient |
| `clankandclaw/config.py` | **Modify** | Add `StealthConfig` pydantic model + env var injection |
| `config.yaml` | **Modify** | Add `stealth:` section |
| `tests/test_config.py` | **Modify** | Add StealthConfig parsing tests |
| `clankandclaw/core/workers/farcaster_detector_worker.py` | **Modify** | Replace httpx.AsyncClient + headers + jitter with StealthClient |
| `clankandclaw/core/workers/gecko_detector_worker.py` | **Modify** | Same as Farcaster |
| `clankandclaw/utils/image_fetcher.py` | **Modify** | Replace raw socket fetch with StealthClient.get(); keep SSRF validation |
| `clankandclaw/core/supervisor.py` | **Modify** | Pass `stealth_config=config.stealth` to workers |

---

## Task 1: `StealthConfig` — config model + YAML + tests

**Files:**
- Modify: `clankandclaw/config.py`
- Modify: `config.yaml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add `StealthConfig` to `config.py`**

  Open `clankandclaw/config.py`. After the `AppSection` class (line 24), insert:

  ```python
  class StealthConfig(BaseModel):
      enabled: bool = True
      rotate_every: int = 50
      jitter_sigma_pct: float = 0.15
      jitter_min_ms: int = 200
      jitter_max_ms: int = 3000
  ```

- [ ] **Step 2: Add `stealth` field to `AppConfig`**

  In `AppConfig` (around line 97), add the `stealth` field:

  ```python
  class AppConfig(BaseModel):
      app: AppSection = Field(default_factory=AppSection)
      x_detector: XDetectorSection = Field(default_factory=XDetectorSection)
      farcaster_detector: FarcasterDetectorSection = Field(default_factory=FarcasterDetectorSection)
      gecko_detector: GeckoDetectorSection = Field(default_factory=GeckoDetectorSection)
      deployment: DeploymentSection = Field(default_factory=DeploymentSection)
      telegram: TelegramSection = Field(default_factory=TelegramSection)
      stealth: StealthConfig = Field(default_factory=StealthConfig)
      wallets: WalletSection
  ```

- [ ] **Step 3: Add env var injection in `load_config`**

  In `load_config`, before the `wallets` block (around line 183), add:

  ```python
  if "stealth" not in raw:
      raw["stealth"] = {}
  if os.getenv("STEALTH_ENABLED"):
      raw["stealth"]["enabled"] = os.getenv("STEALTH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
  if os.getenv("STEALTH_ROTATE_EVERY"):
      raw["stealth"]["rotate_every"] = int(os.getenv("STEALTH_ROTATE_EVERY", "50"))
  if os.getenv("STEALTH_JITTER_SIGMA_PCT"):
      raw["stealth"]["jitter_sigma_pct"] = float(os.getenv("STEALTH_JITTER_SIGMA_PCT", "0.15"))
  if os.getenv("STEALTH_JITTER_MIN_MS"):
      raw["stealth"]["jitter_min_ms"] = int(os.getenv("STEALTH_JITTER_MIN_MS", "200"))
  if os.getenv("STEALTH_JITTER_MAX_MS"):
      raw["stealth"]["jitter_max_ms"] = int(os.getenv("STEALTH_JITTER_MAX_MS", "3000"))
  ```

- [ ] **Step 4: Add `stealth:` section to `config.yaml`**

  Append at the end of `config.yaml`:

  ```yaml
  stealth:
    enabled: true
    rotate_every: 50        # rotate UA after this many requests
    jitter_sigma_pct: 0.15  # gaussian σ as fraction of base wait interval
    jitter_min_ms: 200      # floor on jitter delay
    jitter_max_ms: 3000     # ceiling on jitter delay
  ```

- [ ] **Step 5: Write failing tests**

  Open `tests/test_config.py`. Add to the end of the file:

  ```python
  def test_stealth_config_defaults(tmp_path, monkeypatch):
      monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "a" * 64)
      monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x" + "b" * 40)
      monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x" + "c" * 40)
      cfg_path = tmp_path / "config.yaml"
      cfg_path.write_text("wallets: {}\n")
      config = load_config(cfg_path)
      assert config.stealth.enabled is True
      assert config.stealth.rotate_every == 50
      assert config.stealth.jitter_sigma_pct == 0.15
      assert config.stealth.jitter_min_ms == 200
      assert config.stealth.jitter_max_ms == 3000


  def test_stealth_config_yaml_override(tmp_path, monkeypatch):
      monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "a" * 64)
      monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x" + "b" * 40)
      monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x" + "c" * 40)
      cfg_path = tmp_path / "config.yaml"
      cfg_path.write_text("stealth:\n  enabled: false\n  rotate_every: 10\n")
      config = load_config(cfg_path)
      assert config.stealth.enabled is False
      assert config.stealth.rotate_every == 10


  def test_stealth_config_env_override(tmp_path, monkeypatch):
      monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "a" * 64)
      monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x" + "b" * 40)
      monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x" + "c" * 40)
      monkeypatch.setenv("STEALTH_ENABLED", "false")
      monkeypatch.setenv("STEALTH_ROTATE_EVERY", "25")
      monkeypatch.setenv("STEALTH_JITTER_MIN_MS", "500")
      cfg_path = tmp_path / "config.yaml"
      cfg_path.write_text("")
      config = load_config(cfg_path)
      assert config.stealth.enabled is False
      assert config.stealth.rotate_every == 25
      assert config.stealth.jitter_min_ms == 500
  ```

- [ ] **Step 6: Run tests to verify they fail**

  ```bash
  /opt/homebrew/bin/pytest tests/test_config.py::test_stealth_config_defaults tests/test_config.py::test_stealth_config_yaml_override tests/test_config.py::test_stealth_config_env_override -v
  ```

  Expected: FAIL — `AppConfig` has no `stealth` field yet.

- [ ] **Step 7: Run tests after implementation to verify they pass**

  ```bash
  /opt/homebrew/bin/pytest tests/test_config.py -v
  ```

  Expected: all config tests pass.

- [ ] **Step 8: Commit**

  ```bash
  git add clankandclaw/config.py config.yaml tests/test_config.py
  git commit -m "feat: add StealthConfig to config and config.yaml"
  ```

---

## Task 2: `ua_profiles.py` — UA + header database

**Files:**
- Create: `clankandclaw/utils/ua_profiles.py`

- [ ] **Step 1: Create the file**

  Create `clankandclaw/utils/ua_profiles.py` with this content:

  ```python
  """Browser UA profiles with matching HTTP header sets for stealth HTTP requests."""

  import random
  from dataclasses import dataclass, field


  @dataclass(frozen=True)
  class UAProfile:
      ua: str
      weight: int
      headers: dict[str, str]


  _PROFILES: list[UAProfile] = [
      UAProfile(
          ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          weight=45,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.9",
              "accept-encoding": "gzip, deflate, br",
              "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
              "sec-ch-ua-mobile": "?0",
              "sec-ch-ua-platform": '"Windows"',
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          weight=20,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.9",
              "accept-encoding": "gzip, deflate, br",
              "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
              "sec-ch-ua-mobile": "?0",
              "sec-ch-ua-platform": '"macOS"',
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
          weight=12,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.5",
              "accept-encoding": "gzip, deflate, br",
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
              "te": "trailers",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
          weight=8,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.5",
              "accept-encoding": "gzip, deflate, br",
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
              "te": "trailers",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
          weight=8,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.9",
              "accept-encoding": "gzip, deflate, br",
              "sec-ch-ua": '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
              "sec-ch-ua-mobile": "?0",
              "sec-ch-ua-platform": '"Windows"',
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
          weight=5,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.9",
              "accept-encoding": "gzip, deflate, br",
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
          },
      ),
      UAProfile(
          ua="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          weight=2,
          headers={
              "accept": "application/json, text/plain, */*",
              "accept-language": "en-US,en;q=0.9",
              "accept-encoding": "gzip, deflate, br",
              "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
              "sec-ch-ua-mobile": "?0",
              "sec-ch-ua-platform": '"Linux"',
              "sec-fetch-dest": "empty",
              "sec-fetch-mode": "cors",
              "sec-fetch-site": "cross-site",
              "connection": "keep-alive",
          },
      ),
  ]

  _WEIGHTS: list[int] = [p.weight for p in _PROFILES]


  def pick_profile(exclude_ua: str | None = None) -> UAProfile:
      """Pick a profile by weighted random. If exclude_ua is set, picks a different one."""
      if exclude_ua is not None and len(_PROFILES) > 1:
          candidates = [p for p in _PROFILES if p.ua != exclude_ua]
          weights = [p.weight for p in candidates]
          return random.choices(candidates, weights=weights, k=1)[0]
      return random.choices(_PROFILES, weights=_WEIGHTS, k=1)[0]
  ```

- [ ] **Step 2: Verify it imports cleanly**

  ```bash
  /opt/homebrew/bin/pytest --collect-only -q 2>&1 | head -5
  ```

  Expected: no import errors.

- [ ] **Step 3: Commit**

  ```bash
  git add clankandclaw/utils/ua_profiles.py
  git commit -m "feat: add browser UA profile database for stealth requests"
  ```

---

## Task 3: `StealthClient` — core wrapper + tests

**Files:**
- Create: `clankandclaw/utils/stealth_client.py`
- Create: `tests/utils/test_stealth_client.py`

- [ ] **Step 1: Write failing tests first**

  Create `tests/utils/test_stealth_client.py`:

  ```python
  """Tests for StealthClient."""

  import asyncio
  import pytest
  from unittest.mock import AsyncMock, MagicMock, patch

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
          with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
              mock_response = MagicMock()
              mock_response.status_code = 200
              mock_get.return_value = mock_response
              for _ in range(3):
                  await client.get("https://example.com")
          # After rotate_every requests, UA should have rotated
          assert client.current_ua != first_ua or len(set(
              p.ua for p in __import__("clankandclaw.utils.ua_profiles", fromlist=["_PROFILES"])._PROFILES
          )) == 1


  @pytest.mark.asyncio
  async def test_ua_rotates_on_403(config):
      """UA must rotate immediately on 403."""
      async with StealthClient(config, timeout=5.0) as client:
          first_ua = client.current_ua
          client.on_response(403)
          # With 7 profiles, rotation is virtually guaranteed to pick a different one
          # Run 10 times to confirm it always picks differently
          all_uas = set()
          for _ in range(10):
              client.on_response(403)
              all_uas.add(client.current_ua)
          # Should see at least 2 different UAs across 10 rotations
          assert len(all_uas) >= 2


  @pytest.mark.asyncio
  async def test_ua_rotates_on_429(config):
      """UA must rotate immediately on 429."""
      async with StealthClient(config, timeout=5.0) as client:
          results = set()
          for _ in range(10):
              client.on_response(429)
              results.add(client.current_ua)
          assert len(results) >= 2


  @pytest.mark.asyncio
  async def test_headers_match_ua_chrome_has_sec_ch(config):
      """Chrome UA profiles include sec-ch-ua headers; Firefox does not."""
      async with StealthClient(config, timeout=5.0) as client:
          # Force a Chrome profile by picking until we get one
          for _ in range(50):
              if "Chrome" in client.current_ua and "Edg" not in client.current_ua and "Firefox" not in client.current_ua:
                  break
              client.on_response(429)
          if "Chrome" in client.current_ua:
              headers = client._merged_headers(None)
              assert "sec-ch-ua" in headers


  @pytest.mark.asyncio
  async def test_headers_firefox_no_sec_ch(config):
      """Firefox UA profiles must NOT have sec-ch-ua headers."""
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
  async def test_sleep_jitter_within_bounds(config):
      """sleep_jitter must always sleep within [jitter_min_ms, jitter_max_ms]."""
      config_tight = StealthConfig(
          enabled=True,
          rotate_every=50,
          jitter_sigma_pct=0.5,  # high sigma to stress-test clamping
          jitter_min_ms=100,
          jitter_max_ms=500,
      )
      async with StealthClient(config_tight, timeout=5.0) as client:
          sleep_calls: list[float] = []
          original_sleep = asyncio.sleep

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
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  /opt/homebrew/bin/pytest tests/utils/test_stealth_client.py -v 2>&1 | head -30
  ```

  Expected: FAIL — `StealthClient` not found.

- [ ] **Step 3: Create `clankandclaw/utils/stealth_client.py`**

  ```python
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
          self._request_count += 1
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
          self._request_count += 1
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
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  /opt/homebrew/bin/pytest tests/utils/test_stealth_client.py -v
  ```

  Expected: all pass. If `test_ua_rotates_after_n_requests` is flaky due to randomness with 7 profiles, the assertion is written to tolerate the edge case.

- [ ] **Step 5: Commit**

  ```bash
  git add clankandclaw/utils/stealth_client.py tests/utils/test_stealth_client.py
  git commit -m "feat: add StealthClient with UA rotation and Gaussian jitter"
  ```

---

## Task 4: Integrate into `FarcasterDetectorWorker`

**Files:**
- Modify: `clankandclaw/core/workers/farcaster_detector_worker.py`
- Modify: `clankandclaw/core/supervisor.py` (find instantiation, add `stealth_config`)

- [ ] **Step 1: Update imports in `farcaster_detector_worker.py`**

  Replace the existing imports block at the top:

  ```python
  """Farcaster detector worker for polling and processing Farcaster signals."""

  import asyncio
  import logging
  from collections import deque
  from datetime import datetime, timedelta, timezone
  from concurrent.futures import ThreadPoolExecutor
  import inspect
  from time import perf_counter
  from typing import Any

  import httpx

  from clankandclaw.config import StealthConfig
  from clankandclaw.core.detectors.farcaster_detector import normalize_farcaster_event
  from clankandclaw.core.pipeline import process_candidate
  from clankandclaw.database.manager import DatabaseManager
  from clankandclaw.utils.stealth_client import StealthClient
  ```

  (Removed: `import random`. Added: `StealthConfig`, `StealthClient` imports.)

- [ ] **Step 2: Update `__init__` signature and body**

  In `__init__`, add `stealth_config: StealthConfig | None = None` parameter after `max_pending_notifications`:

  ```python
  def __init__(
      self,
      db: DatabaseManager,
      *,
      poll_interval: float = 35.0,
      api_url: str = "https://api.neynar.com/v2/farcaster/cast/search/",
      api_key: str | None = None,
      max_results: int = 20,
      target_handles: list[str] | None = None,
      query_terms: list[str] | None = None,
      request_timeout_seconds: float = 20.0,
      max_requests_per_minute: int = 45,
      max_process_concurrency: int = 8,
      max_query_concurrency: int = 2,
      loop_timeout_seconds: float = 90.0,
      candidate_process_timeout_seconds: float = 20.0,
      max_pending_notifications: int = 500,
      stealth_config: StealthConfig | None = None,
  ):
  ```

  Remove `user_agent: str = "ClankAndClaw/1.0 (+ops)"` from the signature.

  In the body, replace:
  ```python
  self.user_agent = user_agent
  ...
  self._http_client: httpx.AsyncClient | None = None
  ...
  self._request_jitter_seconds = 0.2
  self._default_headers = {
      "accept": "application/json",
      "accept-language": "en-US,en;q=0.9",
      "user-agent": self.user_agent,
      "connection": "keep-alive",
  }
  ```

  With:
  ```python
  self._stealth_config = stealth_config or StealthConfig()
  self._stealth: StealthClient | None = None
  ```

- [ ] **Step 3: Update `start()` and `stop()`**

  Replace in `start()`:
  ```python
  self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout_seconds))
  ```
  With:
  ```python
  self._stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
  ```

  Replace in `stop()`:
  ```python
  if self._http_client:
      await self._http_client.aclose()
      self._http_client = None
  ```
  With:
  ```python
  if self._stealth:
      await self._stealth.aclose()
      self._stealth = None
  ```

- [ ] **Step 4: Update `_poll_and_process()`**

  Replace:
  ```python
  headers = dict(self._default_headers)
  if self.api_key:
      headers["x-api-key"] = self.api_key
  ...
  created_local_client = False
  client = self._http_client
  if client is None:
      client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout_seconds))
      created_local_client = True
  try:
      processed_count = 0
      query_tasks = [
          asyncio.create_task(self._run_query(client, headers, query))
          for query in self._build_queries()
      ]
      ...
  finally:
      if created_local_client:
          close = getattr(client, "aclose", None)
          if callable(close):
              maybe_awaitable = close()
              if inspect.isawaitable(maybe_awaitable):
                  await maybe_awaitable
  ```

  With:
  ```python
  api_headers: dict[str, str] = {}
  if self.api_key:
      api_headers["x-api-key"] = self.api_key

  stealth = self._stealth
  if stealth is None:
      stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)

  processed_count = 0
  query_tasks = [
      asyncio.create_task(self._run_query(stealth, api_headers, query))
      for query in self._build_queries()
  ]
  if query_tasks:
      results = await asyncio.gather(*query_tasks, return_exceptions=True)
      for result in results:
          if isinstance(result, Exception):
              logger.error("Farcaster query task failed: %s", result, exc_info=True)
              continue
          processed_count += int(result)
  ```

  Also remove the `import inspect` from the imports (no longer needed).

- [ ] **Step 5: Update `_respect_rate_limit()`**

  Replace the entire method:
  ```python
  async def _respect_rate_limit(self) -> None:
      min_interval = (60.0 / float(self.max_requests_per_minute)) * self._request_interval_multiplier
      if not self._last_request_at:
          return
      elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
      if elapsed < min_interval:
          jitter = random.uniform(0.0, self._request_jitter_seconds)
          await asyncio.sleep((min_interval - elapsed) + jitter)
  ```

  With:
  ```python
  async def _respect_rate_limit(self, stealth: StealthClient) -> None:
      min_interval = (60.0 / float(self.max_requests_per_minute)) * self._request_interval_multiplier
      if not self._last_request_at:
          return
      elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
      if elapsed < min_interval:
          await stealth.sleep_jitter(min_interval - elapsed)
  ```

- [ ] **Step 6: Update `_request_with_retry()`**

  Replace signature and body:
  ```python
  async def _request_with_retry(
      self,
      stealth: StealthClient,
      api_headers: dict[str, str],
      params: dict[str, Any],
  ) -> httpx.Response:
      for attempt in range(3):
          await self._respect_rate_limit(stealth)
          response = await stealth.get(self.api_url, headers=api_headers, params=params)
          self._last_request_at = datetime.now(timezone.utc)
          stealth.on_response(response.status_code)
          if response.status_code in {403, 429, 500, 502, 503, 504} and attempt < 2:
              self._on_request_failure(response.status_code)
              await asyncio.sleep(0.35 * (attempt + 1))
              continue
          if response.is_success:
              self._on_request_success()
          elif response.status_code >= 400:
              self._on_request_failure(response.status_code)
          return response
      self._on_request_failure()
      return response
  ```

- [ ] **Step 7: Update `_run_query()` signature**

  Replace first two lines of `_run_query`:
  ```python
  async def _run_query(
      self,
      stealth: StealthClient,
      api_headers: dict[str, str],
      query: str,
  ) -> int:
      async with self._query_semaphore:
          params = {"q": query, "limit": self.max_results}
          response = await self._request_with_retry(stealth, api_headers, params)
  ```

- [ ] **Step 8: Update supervisor to pass `stealth_config`**

  Open `clankandclaw/core/supervisor.py`. Find where `FarcasterDetectorWorker` is instantiated. Add `stealth_config=self._config.stealth` to the constructor call.

  Example — find a line like:
  ```python
  farcaster_worker = FarcasterDetectorWorker(
      db=self.db,
      poll_interval=cfg.poll_interval,
      ...
  )
  ```
  Add:
  ```python
      stealth_config=self._config.stealth,
  ```

- [ ] **Step 9: Run existing Farcaster worker tests**

  ```bash
  /opt/homebrew/bin/pytest tests/core/test_farcaster_detector_worker.py -v --tb=short
  ```

  Expected: all pass. If any test mocks `httpx.AsyncClient.get`, update it to mock `StealthClient.get` or use `respx` / patch at the httpx level.

- [ ] **Step 10: Commit**

  ```bash
  git add clankandclaw/core/workers/farcaster_detector_worker.py clankandclaw/core/supervisor.py
  git commit -m "feat: integrate StealthClient into FarcasterDetectorWorker"
  ```

---

## Task 5: Integrate into `GeckoDetectorWorker`

**Files:**
- Modify: `clankandclaw/core/workers/gecko_detector_worker.py`
- Modify: `clankandclaw/core/supervisor.py` (add `stealth_config` to Gecko instantiation)

- [ ] **Step 1: Update imports**

  Replace the imports block — same changes as Farcaster: remove `import random`, add `StealthConfig` and `StealthClient`.

  ```python
  """GeckoTerminal detector worker for polling and processing hot new-pool signals."""

  import asyncio
  import logging
  from collections import deque
  from datetime import datetime, timedelta, timezone
  from concurrent.futures import ThreadPoolExecutor
  from time import perf_counter
  from typing import Any

  import httpx

  from clankandclaw.config import StealthConfig
  from clankandclaw.core.detectors.gecko_detector import normalize_gecko_payload
  from clankandclaw.core.pipeline import process_candidate
  from clankandclaw.database.manager import DatabaseManager
  from clankandclaw.utils.stealth_client import StealthClient
  ```

- [ ] **Step 2: Update `__init__` signature**

  Remove `user_agent: str = "ClankAndClaw/1.0 (+ops)"` parameter.
  Add `stealth_config: StealthConfig | None = None` at the end.

  In the body, remove:
  ```python
  self.user_agent = user_agent
  ...
  self._request_jitter_seconds = 0.2
  self._default_headers = {
      "accept": "application/json",
      "accept-language": "en-US,en;q=0.9",
      "user-agent": self.user_agent,
      "connection": "keep-alive",
  }
  ```

  Replace `self._http_client: httpx.AsyncClient | None = None` with:
  ```python
  self._stealth_config = stealth_config or StealthConfig()
  self._stealth: StealthClient | None = None
  ```

- [ ] **Step 3: Update `start()` and `stop()`**

  In `start()`, replace:
  ```python
  self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout_seconds))
  ```
  With:
  ```python
  self._stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
  ```

  In `stop()`, replace:
  ```python
  if self._http_client:
      await self._http_client.aclose()
      self._http_client = None
  ```
  With:
  ```python
  if self._stealth:
      await self._stealth.aclose()
      self._stealth = None
  ```

- [ ] **Step 4: Update `_respect_rate_limit()`**

  Replace:
  ```python
  async def _respect_rate_limit(self) -> None:
      min_interval = self._base_request_interval_seconds * self._adaptive_interval_multiplier
      if not self._last_request_at:
          return
      elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
      if elapsed < min_interval:
          jitter = random.uniform(0.0, self._request_jitter_seconds)
          await asyncio.sleep((min_interval - elapsed) + jitter)
  ```

  With:
  ```python
  async def _respect_rate_limit(self, stealth: StealthClient) -> None:
      min_interval = self._base_request_interval_seconds * self._adaptive_interval_multiplier
      if not self._last_request_at:
          return
      elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
      if elapsed < min_interval:
          await stealth.sleep_jitter(min_interval - elapsed)
  ```

- [ ] **Step 5: Update `_poll_network()`**

  Replace signature and body to use `stealth: StealthClient`:

  ```python
  async def _poll_network(self, stealth: StealthClient, network: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
      url = f"{self.api_base_url}/networks/{network}/new_pools"
      for attempt in range(3):
          await self._respect_rate_limit(stealth)
          response = await stealth.get(url, params={"page": 1})
          self._last_request_at = datetime.now(timezone.utc)
          stealth.on_response(response.status_code)
          if response.status_code in {403, 429, 500, 502, 503, 504} and attempt < 2:
              self._on_poll_failure()
              await asyncio.sleep(0.35 * (attempt + 1))
              continue
          response.raise_for_status()
          self._on_poll_success()
          payload = response.json()
          return list(payload.get("data", [])[: self.max_results]), payload.get("included", [])
      self._on_poll_failure()
      return [], []
  ```

- [ ] **Step 6: Update `_poll_and_process()` to pass stealth**

  Find where `_poll_network` is called in `_poll_and_process`. Update all call sites to pass `self._stealth` (or a local stealth if `self._stealth` is None):

  At the top of `_poll_and_process`, add:
  ```python
  stealth = self._stealth
  if stealth is None:
      stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
  ```

  Change all `self._poll_network(client, network)` calls to `self._poll_network(stealth, network)`.

- [ ] **Step 7: Update supervisor**

  In `clankandclaw/core/supervisor.py`, find where `GeckoDetectorWorker` is instantiated and add `stealth_config=self._config.stealth`.

- [ ] **Step 8: Run existing Gecko worker tests**

  ```bash
  /opt/homebrew/bin/pytest tests/core/test_gecko_detector_worker.py -v --tb=short
  ```

  Expected: all pass.

- [ ] **Step 9: Commit**

  ```bash
  git add clankandclaw/core/workers/gecko_detector_worker.py clankandclaw/core/supervisor.py
  git commit -m "feat: integrate StealthClient into GeckoDetectorWorker"
  ```

---

## Task 6: Integrate into `image_fetcher.py`

**Files:**
- Modify: `clankandclaw/utils/image_fetcher.py`
- Modify: `tests/utils/test_image_fetcher.py` (update mocking approach)

- [ ] **Step 1: Rewrite `image_fetcher.py`**

  Replace the entire file content. Keep: `_resolve_fetch_target`, all SSRF helpers (`_is_unsafe_host`, `_is_unsafe_ip_address`, `_is_unsafe_ip`, `_validate_image_url`), `_validate_content_type`, `_validate_size`, `_is_redirect`, `_format_host_header`.

  Remove: `_send_pinned_request`, `_build_request_bytes`, `_read_limited_body`, `_PinnedResponse`.

  New `fetch_image_bytes`:

  ```python
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
      _own_stealth = False
      if stealth is None:
          stealth = StealthClient(StealthConfig(), timeout=REQUEST_TIMEOUT_SECONDS)
          _own_stealth = True

      try:
          return await _fetch_with_stealth(url, stealth)
      finally:
          if _own_stealth:
              await stealth.aclose()


  async def _fetch_with_stealth(url: str, stealth: StealthClient) -> bytes:
      current_url = url
      redirects_followed = 0

      while True:
          # SSRF validation before every request (including redirects)
          await _resolve_fetch_target(current_url)

          response = await stealth.get(
              current_url,
              headers={"accept": "image/*"},
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

      raise ValueError("redirect loop without valid response")


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
  ```

- [ ] **Step 2: Run existing image fetcher tests**

  ```bash
  /opt/homebrew/bin/pytest tests/utils/test_image_fetcher.py -v --tb=short
  ```

  Review failures. Tests that mock `_send_pinned_request` or `asyncio.to_thread` for the fetch step will need updating — they should now mock `StealthClient.get` or patch `httpx.AsyncClient.get`.

  For each failing test that previously used `_send_pinned_request`, update the mock target:

  ```python
  # OLD
  with patch("clankandclaw.utils.image_fetcher._send_pinned_request", ...) as mock:

  # NEW — mock httpx at the transport level using respx, or patch StealthClient.get:
  with patch("clankandclaw.utils.stealth_client.StealthClient.get", new_callable=AsyncMock) as mock_get:
      mock_response = MagicMock(spec=httpx.Response)
      mock_response.status_code = 200
      mock_response.headers = {"content-type": "image/png"}
      mock_response.content = b"fakepng"
      mock_get.return_value = mock_response
  ```

  The SSRF validation tests (testing `_resolve_fetch_target`, `_is_unsafe_host`, etc.) require no changes.

- [ ] **Step 3: Run again to confirm all pass**

  ```bash
  /opt/homebrew/bin/pytest tests/utils/test_image_fetcher.py -v --tb=short
  ```

  Expected: all pass.

- [ ] **Step 4: Commit**

  ```bash
  git add clankandclaw/utils/image_fetcher.py tests/utils/test_image_fetcher.py
  git commit -m "feat: replace raw socket image fetcher with StealthClient"
  ```

---

## Task 7: Full test suite + final commit

**Files:** None (validation only)

- [ ] **Step 1: Run the full test suite**

  ```bash
  /opt/homebrew/bin/pytest --tb=short -q --rootdir="/Users/aaa/Projects/clank and claw v2"
  ```

  Expected: all tests pass. If any fail:
  - Worker tests that pass `user_agent=` kwarg → remove that kwarg from the test constructor call.
  - Tests mocking `self._http_client` → update to mock `self._stealth`.

- [ ] **Step 2: Fix any remaining failures, then re-run**

  ```bash
  /opt/homebrew/bin/pytest --tb=short -q --rootdir="/Users/aaa/Projects/clank and claw v2"
  ```

  Expected: clean pass.

- [ ] **Step 3: Final commit**

  ```bash
  git add -u
  git commit -m "fix: update tests for StealthClient integration"
  ```
