# StealthClient — Anti-Detection HTTP Layer

**Date:** 2026-04-06
**Status:** Approved
**Scope:** Replace ad-hoc HTTP headers and jitter in detectors with a centralized stealth-aware HTTP client.

---

## Problem

The bot makes outbound HTTP requests to Farcaster (Neynar), GeckoTerminal, and various image hosts from a single VPS IP. Current weaknesses:

1. **Static identifiable user agent** — `ClankAndClaw/1.0 (+ops)` and `clankandclaw-image-fetcher/1.0` are trivially blocklist-able.
2. **Minimal headers** — Missing `Sec-Fetch-*`, `Sec-Ch-Ua-*`, `Accept-Encoding`. Profile is too clean to pass as browser traffic.
3. **Predictable jitter** — `random.uniform(0, 0.2)` produces a flat distribution; recognizable by timing analysis.
4. **Duplicated logic** — Each detector manages its own headers and jitter independently. Adding a new detector means copy-pasting boilerplate.

---

## Goals

- Single, centralized HTTP client with anti-detection built in.
- Realistic browser UA + matching header profile, weighted by real market share.
- Human-like Gaussian jitter replacing uniform random.
- Automatic UA rotation on schedule or after 403/429.
- Zero change to detector business logic — only the HTTP layer changes.
- No proxy dependency (proxy-agnostic architecture, can be added later via env var).

---

## Architecture

```
clankandclaw/utils/stealth_client.py   ← core wrapper (NEW)
clankandclaw/utils/ua_profiles.py      ← UA + header database (NEW)
clankandclaw/config.py                 ← StealthConfig dataclass (MODIFIED)
config.yaml                            ← stealth: section (MODIFIED)

Consumers (MODIFIED — HTTP layer only):
  clankandclaw/core/workers/farcaster_detector_worker.py
  clankandclaw/core/workers/gecko_detector_worker.py
  clankandclaw/utils/image_fetcher.py
```

---

## Component: `ua_profiles.py`

Stores a list of `UAProfile` objects. Each profile pairs a realistic browser UA string with its matching HTTP header set, plus a relative `weight` reflecting real-world browser market share.

**Pool (~8 profiles):**

| Profile | Weight |
|---------|--------|
| Chrome 124 / Windows | 45 |
| Chrome 124 / macOS | 20 |
| Firefox 125 / Windows | 12 |
| Firefox 125 / macOS | 8 |
| Edge 124 / Windows | 8 |
| Safari 17 / macOS | 5 |
| Chrome 124 / Linux | 2 |

**Headers per profile include:**

- `user-agent`
- `accept`
- `accept-language` (randomized locale order within profile, e.g. `en-US,en;q=0.9`)
- `accept-encoding: gzip, deflate, br`
- `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform` (Chrome/Edge only)
- `sec-fetch-dest: empty`
- `sec-fetch-mode: cors`
- `sec-fetch-site: cross-site`
- `connection: keep-alive`

Firefox and Safari profiles omit `sec-ch-ua-*` (those headers don't exist in those browsers).

---

## Component: `StealthClient`

Thin wrapper around `httpx.AsyncClient`. Holds one active `UAProfile` and injects it into every request.

### UA Rotation

- **On init:** select profile via weighted random (`random.choices` with weights).
- **Scheduled:** rotate after `rotate_every` requests (default 50).
- **Forced:** rotate immediately on 403 or 429 response, then wait before retry.
- Rotation picks a *different* profile than the current one.

### Header Injection

On every `get()` / `post()` call, `StealthClient` merges the active profile's headers with any caller-supplied headers. Caller headers take precedence (so API keys like `x-api-key` are not overwritten).

### Gaussian Jitter

`StealthClient` exposes an `async sleep_jitter(base_seconds)` helper used by callers before a request:

```
delay = clamp(
    gauss(μ=base_seconds, σ=base_seconds * sigma_pct),
    min=jitter_min_ms / 1000,
    max=jitter_max_ms / 1000,
)
await asyncio.sleep(delay)
```

This replaces `random.uniform(0, 0.2)` in `_respect_rate_limit()`. The existing adaptive multiplier logic in detectors is **unchanged** — Gaussian jitter is applied on top of the calculated base interval.

### Interface

```python
class StealthClient:
    def __init__(self, config: StealthConfig) -> None: ...
    async def get(self, url, *, headers=None, params=None, timeout=None) -> httpx.Response: ...
    async def post(self, url, *, headers=None, data=None, json=None, timeout=None) -> httpx.Response: ...
    async def sleep_jitter(self, base_seconds: float) -> None: ...
    def on_response(self, status_code: int) -> None: ...  # call after each response; triggers rotation on 403/429
    async def aclose(self) -> None: ...
    async def __aenter__(self) -> "StealthClient": ...
    async def __aexit__(self, *args) -> None: ...
```

---

## Config

### `config.yaml` addition

```yaml
stealth:
  enabled: true
  rotate_every: 50        # rotate UA after this many requests
  jitter_sigma_pct: 0.15  # gaussian σ as fraction of base interval
  jitter_min_ms: 200      # floor on jitter delay
  jitter_max_ms: 3000     # ceiling on jitter delay
```

### `StealthConfig` dataclass (added to `config.py`)

```python
@dataclass
class StealthConfig:
    enabled: bool = True
    rotate_every: int = 50
    jitter_sigma_pct: float = 0.15
    jitter_min_ms: int = 200
    jitter_max_ms: int = 3000
```

When `enabled: false`, `StealthClient` falls back to a plain `httpx.AsyncClient` with the original static user agent — useful for local debugging.

---

## Integration Points

### `farcaster_detector_worker.py`

- Remove: `self._default_headers` dict, `self.user_agent` usage in headers, `random.uniform(0, 0.2)` jitter.
- Add: `self._stealth = StealthClient(config.stealth)` in `__init__`.
- Replace: `client.get(...)` → `self._stealth.get(...)`, `_respect_rate_limit` jitter → `self._stealth.sleep_jitter(base)`.
- Call `self._stealth.on_response(status_code)` after each response.

### `gecko_detector_worker.py`

Same changes as Farcaster.

### `image_fetcher.py`

- Remove: raw socket implementation (`_build_raw_request`, `_do_raw_fetch`).
- Keep: SSRF IP validation logic (runs before the request, independent of HTTP client).
- Replace fetch with `StealthClient.get(url, timeout=8.0)`.
- `StealthClient` for image fetcher uses a separate instance with `accept: image/*` added to the caller headers on each call.

---

## What Does NOT Change

- Adaptive rate limiting multiplier (escalate on failure, decay on success) — stays in each detector.
- Circuit breaker logic (Gecko) — unchanged.
- Cooldown timers (90s / 120s on 403/429) — unchanged.
- Request deduplication (seen-ID deques) — unchanged.
- SSRF IP validation in image fetcher — unchanged.
- All Pinata/IPFS calls — Pinata is authenticated + sporadic; not a blocking risk. Left as-is.

---

## Testing

**`tests/utils/test_stealth_client.py`** (new):
- UA rotates after `rotate_every` requests.
- UA rotates on `on_response(403)` and `on_response(429)`.
- Rotated UA is different from previous UA (no repeat on small pools).
- `sleep_jitter` output is within `[jitter_min_ms/1000, jitter_max_ms/1000]` over 1000 samples.
- Header profile matches UA (Chrome UA → has `sec-ch-ua`, Firefox UA → does not).
- When `enabled=false`, returns original static user agent.

**Existing detector tests:** No changes required. They mock at the `httpx` level, which remains the underlying transport.

---

## Out of Scope

- Proxy rotation (deferred — add `PROXY_URL` env var to `StealthClient` later, single-line change).
- TLS/JA3 fingerprint evasion (requires `pyOpenSSL` + custom cipher suites — separate initiative).
- X/Twitter (twscrape manages its own HTTP stack — out of reach without forking the library).
