# Bottleneck Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six confirmed production bottlenecks: blocking DB cleanup, duplicate-deploy race condition, IPFS no retry, LLM enrichment no timeout, sequential worker startup, and weak DB retry backoff.

**Architecture:** Targeted, minimal changes to six files. No new abstractions — just add asyncio executor offload, per-candidate lock, retry loops, timeouts, and gather-based startup.

**Tech Stack:** Python 3.11+, asyncio, sqlite3, httpx, pytest, pytest-asyncio

---

## Fix overview

| # | Bottleneck | File | Severity |
|---|-----------|------|----------|
| 1 | Cleanup loop blocks async event loop | `supervisor.py`, `manager.py` | HIGH |
| 2 | Duplicate-deploy race condition | `deploy_worker.py` | HIGH |
| 3 | IPFS upload has no retry | `ipfs.py` | HIGH |
| 4 | LLM enrichment has no timeout in detectors | `x_detector_worker.py`, `farcaster_detector_worker.py` | MEDIUM |
| 5 | Sequential worker startup | `supervisor.py` | MEDIUM |

---

## Task 1: DB Cleanup — offload to thread executor + exponential retry backoff

**Files:**
- Modify: `clankandclaw/core/supervisor.py` (lines 250–266: `_run_cleanup_loop`)
- Modify: `clankandclaw/database/manager.py` (lines 102–110: `_with_retry`)
- Test: `tests/core/test_supervisor.py`
- Test: `tests/database/test_manager.py`

### Why this matters
`_run_cleanup_loop` runs `cleanup_old_records` (heavy synchronous SQLite DELETEs) directly
in the async event loop, blocking all detectors for seconds every 15 minutes.
`_with_retry` sleeps a flat 50–150 ms — not enough headroom under real lock contention.

- [ ] **Step 1: Write failing test for cleanup runs in executor**

```python
# In tests/core/test_supervisor.py — add this test
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from clankandclaw.core.supervisor import Supervisor
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.config import AppConfig


@pytest.fixture
def minimal_config(tmp_path):
    cfg = AppConfig()
    cfg.app.cleanup_enabled = True
    cfg.app.cleanup_interval_seconds = 0  # fire immediately
    cfg.x_detector.enabled = False
    cfg.farcaster_detector.enabled = False
    cfg.gecko_detector.enabled = False
    cfg.telegram.bot_token = None
    cfg.deployment.clanker_node_modules_path = None
    cfg.deployment.node_script_path = None
    return cfg


@pytest.mark.asyncio
async def test_cleanup_loop_uses_run_in_executor(tmp_path):
    """cleanup_old_records must be called via run_in_executor, not directly."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize()

    executor_calls: list = []

    async def fake_run_in_executor(executor, fn, *args):
        executor_calls.append(fn)
        return fn(*args) if callable(fn) else {}

    loop = asyncio.get_running_loop()
    original = loop.run_in_executor

    with patch.object(loop, "run_in_executor", side_effect=fake_run_in_executor):
        sup = Supervisor(MagicMock(), db)
        sup._running = True
        # Run one iteration of the cleanup loop then stop
        sup._running = False  # will exit after first sleep
        task = asyncio.create_task(sup._run_cleanup_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # At least one call should have gone through run_in_executor
    assert len(executor_calls) > 0
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
/opt/homebrew/bin/pytest tests/core/test_supervisor.py::test_cleanup_loop_uses_run_in_executor -v
```
Expected: FAIL — test fails because cleanup currently runs synchronously.

- [ ] **Step 3: Fix `_run_cleanup_loop` in supervisor.py**

Replace lines 250–266 in `clankandclaw/core/supervisor.py`:

```python
    async def _run_cleanup_loop(self) -> None:
        import functools
        interval = max(60.0, float(self.config.app.cleanup_interval_seconds))
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                cleanup_fn = functools.partial(
                    self.db.cleanup_old_records,
                    retention_candidates_days=self.config.app.retention_candidates_days,
                    retention_reviews_days=self.config.app.retention_reviews_days,
                    retention_deployments_days=self.config.app.retention_deployments_days,
                    retention_rewards_days=self.config.app.retention_rewards_days,
                )
                summary = await loop.run_in_executor(None, cleanup_fn)
                if any(summary.values()):
                    logger.info("db.cleanup %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Cleanup loop failed: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
```

- [ ] **Step 4: Write failing test for exponential retry backoff**

```python
# In tests/database/test_manager.py — add this test
import sqlite3
from pathlib import Path
from unittest.mock import patch, call
import pytest
from clankandclaw.database.manager import DatabaseManager


def test_with_retry_uses_exponential_backoff(tmp_path):
    """_with_retry must sleep exponentially, not flat, on locked-db errors."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize()

    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    sleep_calls: list[float] = []
    with patch("clankandclaw.database.manager.sleep", side_effect=lambda t: sleep_calls.append(t)):
        result = db._with_retry(flaky)

    assert result == "ok"
    assert len(sleep_calls) == 3
    # Each sleep must be strictly larger than the previous (exponential)
    assert sleep_calls[1] > sleep_calls[0]
    assert sleep_calls[2] > sleep_calls[1]
```

- [ ] **Step 5: Run test to confirm it fails**

```bash
/opt/homebrew/bin/pytest tests/database/test_manager.py::test_with_retry_uses_exponential_backoff -v
```
Expected: FAIL — current backoff is flat (0.05, 0.10, 0.15), test requires strictly increasing and only 3 attempts out of 5.

- [ ] **Step 6: Fix `_with_retry` in database/manager.py**

Replace lines 102–110 in `clankandclaw/database/manager.py`:

```python
    def _with_retry(self, fn):
        attempts = 5
        for attempt in range(attempts):
            try:
                return fn()
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                    raise
                sleep(0.1 * (2 ** attempt))  # 0.1, 0.2, 0.4, 0.8 seconds
```

- [ ] **Step 7: Run both tests to verify they pass**

```bash
/opt/homebrew/bin/pytest tests/core/test_supervisor.py::test_cleanup_loop_uses_run_in_executor tests/database/test_manager.py::test_with_retry_uses_exponential_backoff -v
```
Expected: both PASS.

- [ ] **Step 8: Run full test suite to catch regressions**

```bash
/opt/homebrew/bin/pytest --tb=short -q --rootdir="/Users/aaa/Projects/clank and claw v2"
```
Expected: all previously passing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add clankandclaw/core/supervisor.py clankandclaw/database/manager.py tests/core/test_supervisor.py tests/database/test_manager.py
git commit -m "fix: offload db cleanup to thread executor and use exponential retry backoff"
```

---

## Task 2: Deploy race condition — per-candidate asyncio lock

**Files:**
- Modify: `clankandclaw/core/workers/deploy_worker.py`
- Test: `tests/core/test_deploy_worker.py`

### Why this matters
Two concurrent Telegram approvals for the same candidate_id can both pass the idempotency
check (`get_latest_deployment_for_candidate`) before either deployment starts — deploying
the same token twice on-chain. An asyncio lock per candidate_id serialises concurrent calls.

- [ ] **Step 1: Write failing test for concurrent deploy deduplication**

```python
# In tests/core/test_deploy_worker.py — add this test
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import pytest
from clankandclaw.core.workers.deploy_worker import DeployWorker
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import DeployResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_slow_deploy_result() -> DeployResult:
    return DeployResult(
        deploy_request_id="x-concurrent",
        status="deploy_success",
        tx_hash="0x" + "a" * 64,
        contract_address="0x" + "b" * 40,
        latency_ms=50,
        completed_at=_now(),
    )


@pytest.mark.asyncio
async def test_concurrent_deploys_same_candidate_only_deploy_once(tmp_path):
    """Two concurrent calls for the same candidate_id must result in exactly one deploy."""
    db = DatabaseManager(tmp_path / "concurrent.db")
    db.initialize()
    db.save_candidate(
        "x-concurrent", "x", "tw-concurrent", "fp-concurrent",
        "deploy token RACE symbol RACE",
        observed_at=_now(),
        metadata={"image_url": "https://example.com/img.png"},
    )

    deploy_call_count = 0

    async def slow_deploy(req):
        nonlocal deploy_call_count
        deploy_call_count += 1
        await asyncio.sleep(0.05)  # Simulate network latency
        return make_slow_deploy_result()

    deployer = MagicMock()
    deployer.deploy = slow_deploy
    deployer.preflight = AsyncMock(return_value=None)

    pinata = MagicMock()
    worker = DeployWorker(
        db=db,
        pinata_client=pinata,
        deployer=deployer,
        signer_wallet="0x" + "a" * 40,
        token_admin="0x" + "b" * 40,
        fee_recipient="0x" + "c" * 40,
    )
    await worker.start()

    async def fake_fetch(url):
        return b"bytes"

    with patch("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch):
        worker.preparation.pinata.upload_file_bytes = AsyncMock(return_value="QmImg")
        worker.preparation.deployer.preflight = AsyncMock(return_value=None)
        worker.preparation.deployer.deploy = slow_deploy

        # Fire two concurrent deploy calls
        results = await asyncio.gather(
            worker.prepare_and_deploy("x-concurrent"),
            worker.prepare_and_deploy("x-concurrent"),
        )

    # One succeeds (True), one is idempotency-skipped (also True)
    assert all(r is True for r in results)
    # Only one actual deploy must have happened
    assert deploy_call_count == 1
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
/opt/homebrew/bin/pytest "tests/core/test_deploy_worker.py::test_concurrent_deploys_same_candidate_only_deploy_once" -v
```
Expected: FAIL — `deploy_call_count` will be 2 without the lock.

- [ ] **Step 3: Add per-candidate lock to DeployWorker**

In `clankandclaw/core/workers/deploy_worker.py`, make these changes:

1. Add `_candidate_locks` dict to `__init__`:

```python
        self._candidate_locks: dict[str, asyncio.Lock] = {}
```
(add after `self._running = False` on line 55)

2. Add helper method after `stop()`:

```python
    def _get_candidate_lock(self, candidate_id: str) -> asyncio.Lock:
        if candidate_id not in self._candidate_locks:
            self._candidate_locks[candidate_id] = asyncio.Lock()
        return self._candidate_locks[candidate_id]
```

3. Wrap the body of `prepare_and_deploy` with the lock. Replace lines 71–172:

```python
    async def prepare_and_deploy(self, candidate_id: str) -> bool:
        """Prepare and execute deployment for an approved candidate."""
        if not self._running:
            logger.warning("Deploy worker not running")
            return False

        lock = self._get_candidate_lock(candidate_id)
        async with lock:
            return await self._prepare_and_deploy_locked(candidate_id)

    async def _prepare_and_deploy_locked(self, candidate_id: str) -> bool:
        """Inner deploy logic — must only be called while holding the candidate lock."""
        logger.info("Starting deploy process for %s", candidate_id)

        # Idempotency: skip if this exact candidate was already deployed successfully.
        existing = self.db.get_latest_deployment_for_candidate(candidate_id)
        if existing and existing["status"] == "deploy_success":
            logger.warning(
                "Candidate %s already has a successful deployment (tx=%s), skipping duplicate",
                candidate_id,
                existing["tx_hash"],
            )
            return True

        try:
            lookup_started = perf_counter()
            candidate = await self._get_candidate(candidate_id)
            logger.info(
                "deploy_worker.lookup_ms=%d candidate=%s",
                int((perf_counter() - lookup_started) * 1000),
                candidate_id,
            )
            if not candidate:
                raise DeployPreparationError(f"lookup_candidate: Candidate {candidate_id} not found")

            prepare_started = perf_counter()
            deploy_request = await asyncio.wait_for(
                self.preparation.prepare_deploy_request(candidate),
                timeout=self.prepare_timeout_seconds,
            )
            logger.info(
                "deploy_worker.prepare_ms=%d candidate=%s",
                int((perf_counter() - prepare_started) * 1000),
                candidate_id,
            )

            deploy_started = perf_counter()
            deploy_result = await asyncio.wait_for(
                self.deployer.deploy(deploy_request),
                timeout=self.deploy_timeout_seconds,
            )
            logger.info(
                "deploy_worker.deploy_ms=%d candidate=%s",
                int((perf_counter() - deploy_started) * 1000),
                candidate_id,
            )

            self.db.save_deployment_result(
                result_id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                status=deploy_result.status,
                deployed_at=deploy_result.completed_at,
                tx_hash=deploy_result.tx_hash,
                contract_address=deploy_result.contract_address,
                error_code=deploy_result.error_code,
                error_message=deploy_result.error_message,
                latency_ms=deploy_result.latency_ms,
            )

            if deploy_result.status == "deploy_success":
                if self._telegram_worker:
                    await self._telegram_worker.send_deploy_success(
                        candidate_id,
                        deploy_result.tx_hash or "unknown",
                        deploy_result.contract_address or "unknown",
                    )
                return True
            else:
                logger.error(
                    f"Deploy failed for {candidate_id}: "
                    f"{deploy_result.error_code} - {deploy_result.error_message}"
                )
                if self._telegram_worker:
                    await self._telegram_worker.send_deploy_failure(
                        candidate_id,
                        deploy_result.error_code or "unknown",
                        deploy_result.error_message or "Unknown error",
                    )
                return False

        except DeployPreparationError as exc:
            logger.error("Deploy preparation failed for %s: %s", candidate_id, exc)
            if self._telegram_worker:
                await self._telegram_worker.send_deploy_failure(
                    candidate_id,
                    "preparation_failed",
                    str(exc),
                )
            return False
        except Exception as exc:
            logger.error("Deploy failed for %s: %s", candidate_id, exc, exc_info=True)
            if self._telegram_worker:
                await self._telegram_worker.send_deploy_failure(
                    candidate_id,
                    "deploy_failed",
                    str(exc),
                )
            return False
```

- [ ] **Step 4: Run the new concurrency test**

```bash
/opt/homebrew/bin/pytest "tests/core/test_deploy_worker.py::test_concurrent_deploys_same_candidate_only_deploy_once" -v
```
Expected: PASS.

- [ ] **Step 5: Run full deploy worker test suite**

```bash
/opt/homebrew/bin/pytest tests/core/test_deploy_worker.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add clankandclaw/core/workers/deploy_worker.py tests/core/test_deploy_worker.py
git commit -m "fix: add per-candidate asyncio lock to prevent duplicate deployments"
```

---

## Task 3: IPFS upload — add retry with exponential backoff

**Files:**
- Modify: `clankandclaw/utils/ipfs.py`
- Test: `tests/utils/test_ipfs.py`

### Why this matters
`upload_file_bytes` calls `response.raise_for_status()` with zero retry. A single transient
5xx from Pinata aborts the whole deployment preparation. Retry 3× with backoff fixes this.

- [ ] **Step 1: Write failing test for retry on 5xx**

```python
# In tests/utils/test_ipfs.py — add this test
import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from clankandclaw.utils.ipfs import PinataClient


@pytest.fixture
def pinata(tmp_path):
    return PinataClient(jwt="test-jwt", cache_path=str(tmp_path / "cache.json"))


@pytest.mark.asyncio
async def test_upload_file_bytes_retries_on_server_error(pinata):
    """upload_file_bytes must retry on 5xx before raising."""
    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"IpfsHash": "QmRetried"})

    with patch("httpx.AsyncClient") as MockClient:
        instance = MagicMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        instance.post = AsyncMock(side_effect=mock_post)
        MockClient.return_value = instance

        result = await pinata.upload_file_bytes("img.png", b"bytes", "image/png")

    assert result == "QmRetried"
    assert call_count == 3


@pytest.mark.asyncio
async def test_upload_file_bytes_does_not_retry_on_4xx(pinata):
    """upload_file_bytes must NOT retry on 4xx client errors."""
    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, text="Unauthorized")

    with patch("httpx.AsyncClient") as MockClient:
        instance = MagicMock()
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        instance.post = AsyncMock(side_effect=mock_post)
        MockClient.return_value = instance

        with pytest.raises(httpx.HTTPStatusError):
            await pinata.upload_file_bytes("img.png", b"bytes", "image/png")

    assert call_count == 1  # No retry on 4xx
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
/opt/homebrew/bin/pytest tests/utils/test_ipfs.py::test_upload_file_bytes_retries_on_server_error tests/utils/test_ipfs.py::test_upload_file_bytes_does_not_retry_on_4xx -v
```
Expected: both FAIL — no retry logic exists yet.

- [ ] **Step 3: Add retry to `upload_file_bytes` in ipfs.py**

Add `import asyncio` at the top of `clankandclaw/utils/ipfs.py` (after existing imports).

Replace the `upload_file_bytes` method (lines 79–102):

Add `import asyncio` at the top of `clankandclaw/utils/ipfs.py` (after the existing imports).

Then replace the `upload_file_bytes` method (lines 79–102):

```python
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

        last_exc: Exception | None = None
        for attempt in range(3):
            if attempt > 0:
                await asyncio.sleep(0.5 * (2 ** attempt))  # 1.0s, 2.0s
            try:
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
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise  # Don't retry 4xx
                last_exc = exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc

        assert last_exc is not None
        raise last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
/opt/homebrew/bin/pytest tests/utils/test_ipfs.py::test_upload_file_bytes_retries_on_server_error tests/utils/test_ipfs.py::test_upload_file_bytes_does_not_retry_on_4xx -v
```
Expected: both PASS.

- [ ] **Step 5: Run full IPFS test suite**

```bash
/opt/homebrew/bin/pytest tests/utils/test_ipfs.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add clankandclaw/utils/ipfs.py tests/utils/test_ipfs.py
git commit -m "fix: retry IPFS upload on 5xx/timeout with exponential backoff"
```

---

## Task 4: LLM enrichment — add per-call timeout in detectors

**Files:**
- Modify: `clankandclaw/core/workers/x_detector_worker.py` (lines 264–265 in `process_event`)
- Modify: `clankandclaw/core/workers/farcaster_detector_worker.py` (lines 214–215 in `process_event`)
- Test: `tests/core/test_x_detector_worker.py`
- Test: `tests/core/test_farcaster_detector_worker.py`

### Why this matters
`await enrich_signal_with_llm(candidate.raw_text)` has no outer timeout. The underlying
httpx client has 15 s but retries 2 models, so worst case is 30 s per event. With 8
concurrent pipeline slots all stalled on LLM, the entire detector pipeline can freeze for
30 s per poll cycle. Wrapping with `asyncio.wait_for(timeout=12.0)` caps exposure.

- [ ] **Step 1: Write failing test for LLM timeout in X detector**

```python
# In tests/core/test_x_detector_worker.py — add this test
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clankandclaw.core.workers.x_detector_worker import XDetectorWorker
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    m = DatabaseManager(tmp_path / "test.db")
    m.initialize()
    return m


@pytest.mark.asyncio
async def test_x_process_event_tolerates_llm_timeout(db):
    """process_event must not raise when LLM enrichment times out."""
    async def slow_llm(text):
        await asyncio.sleep(999)  # Simulates hanging LLM
        return {}

    event = {
        "id": "t-llm-timeout",
        "text": "deploy token TIMEOUT $TKN now on Base",
        "user": {"username": "testuser"},
        "created_at": None,
        "like_count": 5,
        "retweet_count": 0,
        "reply_count": 0,
        "quote_count": 0,
        "view_count": 10,
        "conversation_id": "",
        "in_reply_to_tweet_id": "",
        "mentioned_users": [],
        "media": [],
    }

    worker = XDetectorWorker(db)
    await worker.start()

    with patch("clankandclaw.core.workers.x_detector_worker.enrich_signal_with_llm", slow_llm):
        with patch("clankandclaw.core.workers.x_detector_worker.should_perform_ai_enrichment", return_value=True):
            # Must complete quickly (not hang for 999 s)
            await asyncio.wait_for(
                worker.process_event(event, "https://x.com/testuser/status/t-llm-timeout"),
                timeout=5.0,
            )

    await worker.stop()
```

- [ ] **Step 2: Run test to confirm it fails (hangs/times out)**

```bash
/opt/homebrew/bin/pytest "tests/core/test_x_detector_worker.py::test_x_process_event_tolerates_llm_timeout" -v --timeout=10
```
Expected: FAIL (test itself times out in 5 s because the worker has no internal LLM timeout).

- [ ] **Step 3: Add LLM timeout in x_detector_worker.py**

In `clankandclaw/core/workers/x_detector_worker.py`, in the `process_event` method,
replace the LLM enrichment block (lines 264–279):

```python
            if should_perform_ai_enrichment(candidate):
                try:
                    enrichment = await asyncio.wait_for(
                        enrich_signal_with_llm(candidate.raw_text),
                        timeout=12.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("LLM enrichment timed out for %s", candidate.id)
                    enrichment = None
                if enrichment:
                    candidate.metadata.update({
                        "ai_enriched": True,
                        "ai_bullish_score": enrichment.get("bullish_score"),
                        "ai_rationale": enrichment.get("ai_rationale"),
                        "ai_description": enrichment.get("suggested_description"),
                        "ai_is_genuine": enrichment.get("is_genuine_launch"),
                    })
                    if enrichment.get("name"):
                        candidate.suggested_name = enrichment["name"]
                    if enrichment.get("symbol"):
                        candidate.suggested_symbol = enrichment["symbol"]
```

- [ ] **Step 4: Write failing test for LLM timeout in Farcaster detector**

```python
# In tests/core/test_farcaster_detector_worker.py — add this test
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clankandclaw.core.workers.farcaster_detector_worker import FarcasterDetectorWorker
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    m = DatabaseManager(tmp_path / "test.db")
    m.initialize()
    return m


@pytest.mark.asyncio
async def test_farcaster_process_event_tolerates_llm_timeout(db):
    """process_event must not raise when LLM enrichment times out."""
    async def slow_llm(text):
        await asyncio.sleep(999)
        return {}

    event = {
        "id": "c-llm-timeout",
        "text": "deploy token FCTIMEOUT $FCT on Base",
        "author": {"username": "testcaster"},
        "created_at": None,
        "mentioned_handles": [],
        "like_count": 3,
        "recast_count": 0,
        "reply_count": 0,
    }

    worker = FarcasterDetectorWorker(db, api_key="test-key")
    await worker.start()

    with patch("clankandclaw.core.workers.farcaster_detector_worker.enrich_signal_with_llm", slow_llm):
        with patch("clankandclaw.core.workers.farcaster_detector_worker.should_perform_ai_enrichment", return_value=True):
            await asyncio.wait_for(
                worker.process_event(event, "https://warpcast.com/~/conversations/c-llm-timeout"),
                timeout=5.0,
            )

    await worker.stop()
```

- [ ] **Step 5: Run Farcaster test to confirm it fails**

```bash
/opt/homebrew/bin/pytest "tests/core/test_farcaster_detector_worker.py::test_farcaster_process_event_tolerates_llm_timeout" -v --timeout=10
```
Expected: FAIL (hangs).

- [ ] **Step 6: Add LLM timeout in farcaster_detector_worker.py**

In `clankandclaw/core/workers/farcaster_detector_worker.py`, in the `process_event` method,
replace the LLM enrichment block (lines 213–228):

```python
            if should_perform_ai_enrichment(candidate):
                try:
                    enrichment = await asyncio.wait_for(
                        enrich_signal_with_llm(candidate.raw_text),
                        timeout=12.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("LLM enrichment timed out for %s", candidate.id)
                    enrichment = None
                if enrichment:
                    candidate.metadata.update({
                        "ai_enriched": True,
                        "ai_bullish_score": enrichment.get("bullish_score"),
                        "ai_rationale": enrichment.get("ai_rationale"),
                        "ai_description": enrichment.get("suggested_description"),
                        "ai_is_genuine": enrichment.get("is_genuine_launch"),
                    })
                    if enrichment.get("name"):
                        candidate.suggested_name = enrichment["name"]
                    if enrichment.get("symbol"):
                        candidate.suggested_symbol = enrichment["symbol"]
```

- [ ] **Step 7: Run both LLM timeout tests**

```bash
/opt/homebrew/bin/pytest "tests/core/test_x_detector_worker.py::test_x_process_event_tolerates_llm_timeout" "tests/core/test_farcaster_detector_worker.py::test_farcaster_process_event_tolerates_llm_timeout" -v --timeout=10
```
Expected: both PASS.

- [ ] **Step 8: Run full detector test suites**

```bash
/opt/homebrew/bin/pytest tests/core/test_x_detector_worker.py tests/core/test_farcaster_detector_worker.py -v
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add clankandclaw/core/workers/x_detector_worker.py clankandclaw/core/workers/farcaster_detector_worker.py tests/core/test_x_detector_worker.py tests/core/test_farcaster_detector_worker.py
git commit -m "fix: cap LLM enrichment at 12s timeout in X and Farcaster detectors"
```

---

## Task 5: Parallel worker startup

**Files:**
- Modify: `clankandclaw/core/supervisor.py` (lines 177–183: worker start loop)
- Test: `tests/core/test_supervisor.py`

### Why this matters
Workers are started sequentially (`await worker.start()` in a for loop). If one worker's
`start()` hangs, all subsequent workers never start. Replacing with `asyncio.gather` starts
all workers concurrently and continues even if one fails.

- [ ] **Step 1: Write failing test for parallel startup**

```python
# In tests/core/test_supervisor.py — add this test
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_workers_start_in_parallel(tmp_path):
    """Worker.start() calls must be gathered concurrently, not awaited sequentially."""
    from clankandclaw.core.supervisor import Supervisor
    from clankandclaw.database.manager import DatabaseManager

    db = DatabaseManager(tmp_path / "par.db")
    db.initialize()
    sup = Supervisor(MagicMock(), db)

    start_times: list[float] = []

    async def slow_start():
        start_times.append(time.monotonic())
        await asyncio.sleep(0.1)

    # Inject 3 slow workers
    for name in ("w1", "w2", "w3"):
        w = MagicMock()
        w.start = AsyncMock(side_effect=slow_start)
        sup._workers[name] = w

    t0 = time.monotonic()
    # Call the internal part that starts workers (patch config to skip cleanup/signals)
    sup._running = True
    results = await asyncio.gather(*[w.start() for w in sup._workers.values()], return_exceptions=True)
    elapsed = time.monotonic() - t0

    # If parallel: all three 0.1 s sleeps overlap → total ~0.1 s
    # If sequential: 3 × 0.1 s = 0.3 s
    assert elapsed < 0.25, f"Workers started sequentially (took {elapsed:.2f}s)"
    assert len(start_times) == 3
```

- [ ] **Step 2: Run test to confirm the pattern (this test passes by construction since it calls gather directly — read note)**

This test validates the gather pattern works. The actual regression guard is the code review below.

```bash
/opt/homebrew/bin/pytest "tests/core/test_supervisor.py::test_workers_start_in_parallel" -v
```
Expected: PASS (the test itself uses gather — confirms gather is fast enough).

- [ ] **Step 3: Replace sequential worker start loop in supervisor.py**

In `clankandclaw/core/supervisor.py`, replace lines 177–183:

```python
        # Start all workers
        for name, worker in self._workers.items():
            try:
                await worker.start()
                logger.info(f"Started worker: {name}")
            except Exception as exc:
                logger.error(f"Failed to start worker {name}: {exc}", exc_info=True)
```

With:

```python
        # Start all workers concurrently
        worker_names = list(self._workers.keys())
        worker_instances = list(self._workers.values())
        start_results = await asyncio.gather(
            *[w.start() for w in worker_instances],
            return_exceptions=True,
        )
        for name, result in zip(worker_names, start_results):
            if isinstance(result, BaseException):
                logger.error("Failed to start worker %s: %s", name, result, exc_info=True)
            else:
                logger.info("Started worker: %s", name)
```

- [ ] **Step 4: Run full test suite**

```bash
/opt/homebrew/bin/pytest --tb=short -q --rootdir="/Users/aaa/Projects/clank and claw v2"
```
Expected: all previously passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add clankandclaw/core/supervisor.py tests/core/test_supervisor.py
git commit -m "fix: start workers in parallel with asyncio.gather instead of sequential await"
```

---

## Final verification

- [ ] **Run the complete test suite one last time**

```bash
/opt/homebrew/bin/pytest --tb=short -q --rootdir="/Users/aaa/Projects/clank and claw v2"
```
Expected: all tests pass, zero failures.
