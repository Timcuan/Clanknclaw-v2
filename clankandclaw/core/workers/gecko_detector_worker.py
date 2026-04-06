"""GeckoTerminal detector worker for polling and processing hot new-pool signals."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import random
from time import perf_counter
from typing import Any

import httpx

from clankandclaw.core.detectors.gecko_detector import normalize_gecko_payload
from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _pool_age_minutes(pool_created_at: str | None) -> float:
    if not pool_created_at:
        return 0.0
    normalized = pool_created_at[:-1] + "+00:00" if pool_created_at.endswith("Z") else pool_created_at
    created = datetime.fromisoformat(normalized)
    if created.tzinfo is None or created.utcoffset() is None:
        created = created.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - created.astimezone(timezone.utc)).total_seconds() / 60.0)


def _normalize_tag(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


class GeckoDetectorWorker:
    """Worker that polls GeckoTerminal for hot new pools and processes them through the pipeline."""

    def __init__(
        self,
        db: DatabaseManager,
        *,
        poll_interval: float = 25.0,
        api_base_url: str = "https://api.geckoterminal.com/api/v2",
        networks: list[str] | None = None,
        max_results: int = 20,
        max_pool_age_minutes: int = 120,
        min_volume_m5_usd: float = 3000.0,
        min_volume_m15_usd: float = 8000.0,
        min_tx_count_m5: int = 12,
        min_liquidity_usd: float = 12000.0,
        max_requests_per_minute: int = 40,
        request_timeout_seconds: float = 20.0,
        base_target_sources: list[str] | None = None,
        max_process_concurrency: int = 10,
        user_agent: str = "ClankAndClaw/1.0 (+ops)",
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.api_base_url = api_base_url.rstrip("/")
        requested_networks = [net.strip().lower() for net in (networks or ["base", "eth", "solana", "bsc"]) if net.strip()]
        self.networks = self._prioritize_networks(requested_networks)
        self.max_results = max_results
        self.max_pool_age_minutes = max_pool_age_minutes
        self.min_volume_m5_usd = min_volume_m5_usd
        self.min_volume_m15_usd = min_volume_m15_usd
        self.min_tx_count_m5 = min_tx_count_m5
        self.min_liquidity_usd = min_liquidity_usd
        self.max_requests_per_minute = max(1, max_requests_per_minute)
        self.request_timeout_seconds = request_timeout_seconds
        self.max_process_concurrency = max(1, max_process_concurrency)
        self.user_agent = user_agent
        self.base_target_sources = [_normalize_tag(s) for s in (base_target_sources or ["bankr", "doppler", "zora", "virtual", "uniswapv4", "clanker"])]
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None
        self._last_poll_time: datetime | None = None
        self._seen_pool_ids: deque[str] = deque(maxlen=5000)
        self._pool_processed_at: dict[str, datetime] = {}
        self._pool_last_hot_score: dict[str, int] = {}
        self._pool_last_volume_m5: dict[str, float] = {}
        self._last_request_at: datetime | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._process_semaphore = asyncio.Semaphore(self.max_process_concurrency)
        self._notify_semaphore = asyncio.Semaphore(8)
        self._notification_tasks: set[asyncio.Task[Any]] = set()
        self._pipeline_executor = ThreadPoolExecutor(
            max_workers=self.max_process_concurrency,
            thread_name_prefix="xcc-gecko-pipeline",
        )
        self._base_request_interval_seconds = 60.0 / float(self.max_requests_per_minute)
        self._adaptive_interval_multiplier = 1.0
        self._degraded_until: datetime | None = None
        self._circuit_open_until: datetime | None = None
        self._consecutive_poll_errors = 0
        self._consecutive_poll_successes = 0
        self._pool_reprocess_cooldown_seconds = 600
        self._request_jitter_seconds = 0.2
        self._default_headers = {
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": self.user_agent,
            "connection": "keep-alive",
        }

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        if self._running:
            logger.warning("Gecko detector worker already running")
            return

        self._running = True
        self._last_poll_time = datetime.now(timezone.utc)
        self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout_seconds))
        self._task = asyncio.create_task(self._run())
        logger.info("Gecko detector worker started")

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        if self._notification_tasks:
            await asyncio.gather(*list(self._notification_tasks), return_exceptions=True)
        await asyncio.to_thread(self._pipeline_executor.shutdown, True)
        logger.info("Gecko detector worker stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await self._poll_and_process()
            except Exception as exc:
                logger.error(f"Error in Gecko detector worker: {exc}", exc_info=True)
            await asyncio.sleep(self.poll_interval)

    def _prioritize_networks(self, networks: list[str]) -> list[str]:
        priority = {"base": 0, "solana": 1, "bsc": 2, "eth": 3}
        deduped: list[str] = []
        seen: set[str] = set()
        for network in networks:
            if network in seen:
                continue
            seen.add(network)
            deduped.append(network)
        return sorted(deduped, key=lambda net: (priority.get(net, 10), net))

    async def _respect_rate_limit(self) -> None:
        min_interval = self._base_request_interval_seconds * self._adaptive_interval_multiplier
        if not self._last_request_at:
            return
        elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
        if elapsed < min_interval:
            jitter = random.uniform(0.0, self._request_jitter_seconds)
            await asyncio.sleep((min_interval - elapsed) + jitter)

    def _on_poll_success(self) -> None:
        self._consecutive_poll_successes += 1
        self._consecutive_poll_errors = 0
        if self._consecutive_poll_successes >= 3:
            self._adaptive_interval_multiplier = max(1.0, self._adaptive_interval_multiplier * 0.9)
            self._consecutive_poll_successes = 0
        if self._degraded_until and datetime.now(timezone.utc) >= self._degraded_until:
            self._degraded_until = None
        if self._circuit_open_until and datetime.now(timezone.utc) >= self._circuit_open_until:
            self._circuit_open_until = None

    def _on_poll_failure(self) -> None:
        self._consecutive_poll_errors += 1
        self._consecutive_poll_successes = 0
        self._adaptive_interval_multiplier = min(3.0, self._adaptive_interval_multiplier * 1.25)
        if self._consecutive_poll_errors >= 4:
            self._degraded_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=90)
        if self._consecutive_poll_errors >= 7:
            self._circuit_open_until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=180)

    async def _poll_network(self, client: httpx.AsyncClient, network: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        url = f"{self.api_base_url}/networks/{network}/new_pools"
        for attempt in range(3):
            await self._respect_rate_limit()
            response = await client.get(url, params={"page": 1}, headers=self._default_headers)
            self._last_request_at = datetime.now(timezone.utc)
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

    def _build_token_index(self, included: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        token_index: dict[str, dict[str, Any]] = {}
        for item in included:
            if item.get("type") != "token":
                continue
            token_id = str(item.get("id") or "")
            if token_id:
                token_index[token_id] = item.get("attributes", {}) or {}
        return token_index

    def _extract_base_token(self, pool: dict[str, Any], token_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
        rel = (pool.get("relationships") or {}).get("base_token") or {}
        token_data = rel.get("data") or {}
        token_id = str(token_data.get("id") or "")
        if token_id and token_id in token_index:
            return token_index[token_id]
        return {}

    def _profile_for_network(self, network: str) -> dict[str, float]:
        # Chain-specific gameplay profile for momentum detection.
        profiles: dict[str, dict[str, float]] = {
            "base": {"min_volume_m5_usd": 2500.0, "min_volume_m15_usd": 7000.0, "min_tx_count_m5": 10.0, "min_liquidity_usd": 9000.0, "max_pool_age_minutes": 120.0, "min_hot_points": 4.0, "require_target_source": 1.0},
            "solana": {"min_volume_m5_usd": 9000.0, "min_volume_m15_usd": 22000.0, "min_tx_count_m5": 24.0, "min_liquidity_usd": 18000.0, "max_pool_age_minutes": 90.0, "min_hot_points": 4.0, "require_target_source": 0.0},
            "bsc": {"min_volume_m5_usd": 7000.0, "min_volume_m15_usd": 17000.0, "min_tx_count_m5": 20.0, "min_liquidity_usd": 16000.0, "max_pool_age_minutes": 120.0, "min_hot_points": 4.0, "require_target_source": 0.0},
            "eth": {"min_volume_m5_usd": 12000.0, "min_volume_m15_usd": 30000.0, "min_tx_count_m5": 28.0, "min_liquidity_usd": 40000.0, "max_pool_age_minutes": 90.0, "min_hot_points": 4.0, "require_target_source": 0.0},
        }
        base = {
            "min_volume_m5_usd": self.min_volume_m5_usd,
            "min_volume_m15_usd": self.min_volume_m15_usd,
            "min_tx_count_m5": float(self.min_tx_count_m5),
            "min_liquidity_usd": self.min_liquidity_usd,
            "max_pool_age_minutes": float(self.max_pool_age_minutes),
            "min_hot_points": 4.0,
            "require_target_source": 0.0,
        }
        base.update(profiles.get(network, {}))
        return base

    def _base_source_match(self, attrs: dict[str, Any]) -> tuple[int, list[str]]:
        haystack_values = [
            str(attrs.get("dex_id") or ""),
            str(attrs.get("name") or ""),
        ]
        haystack = " ".join(_normalize_tag(v) for v in haystack_values if v)
        matched = [tag for tag in self.base_target_sources if tag and tag in haystack]
        return len(set(matched)), sorted(set(matched))

    def _evaluate_pool(self, network: str, attrs: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
        profile = self._profile_for_network(network)
        volume = attrs.get("volume_usd") or {}
        tx_data = attrs.get("transactions") or {}
        tx_m1 = tx_data.get("m1") or {}
        tx_m5 = tx_data.get("m5") or {}

        volume_m1 = _to_float(volume.get("m1"))
        volume_m5 = _to_float(volume.get("m5"))
        volume_m15 = _to_float(volume.get("m15"))
        tx_count_m1 = _to_int(tx_m1.get("buys")) + _to_int(tx_m1.get("sells"))
        tx_count_m5 = _to_int(tx_m5.get("buys")) + _to_int(tx_m5.get("sells"))
        liquidity_usd = _to_float(attrs.get("reserve_in_usd"))
        age_minutes = _pool_age_minutes(attrs.get("pool_created_at"))
        spike_ratio = (volume_m5 / volume_m15) if volume_m15 > 0 else 0.0
        spike_ratio_m1_m5 = (volume_m1 / volume_m5) if volume_m5 > 0 else 0.0
        base_source_match_score, base_source_tags = self._base_source_match(attrs) if network == "base" else (0, [])
        min_volume_m1 = max(400.0, float(profile["min_volume_m5_usd"]) * 0.2)
        min_tx_count_m1 = max(3, int(float(profile["min_tx_count_m5"]) * 0.4))

        has_m1_signal = volume_m1 > 0 or tx_count_m1 > 0
        # Stage 1: early spike/freshness gate to catch momentum quickly.
        stage1_fast_spike = (
            age_minutes <= float(profile["max_pool_age_minutes"])
            and (
                (
                    has_m1_signal
                    and (
                        (volume_m1 >= min_volume_m1 and tx_count_m1 >= min_tx_count_m1)
                        or (volume_m5 >= float(profile["min_volume_m5_usd"]) * 0.8 and spike_ratio_m1_m5 >= 0.2)
                    )
                )
                or (
                    not has_m1_signal
                    and volume_m5 >= float(profile["min_volume_m5_usd"])
                    and tx_count_m5 >= int(float(profile["min_tx_count_m5"]) * 0.7)
                )
            )
        )
        if not stage1_fast_spike:
            stats = {
                "volume": {"m1": volume_m1, "m5": volume_m5, "m15": volume_m15},
                "transactions": {"m1": tx_count_m1, "m5": tx_count_m5},
                "liquidity_usd": liquidity_usd,
                "pool_created_at": attrs.get("pool_created_at"),
                "pool_age_minutes": age_minutes,
                "spike_ratio": spike_ratio,
                "spike_ratio_m1_m5": spike_ratio_m1_m5,
                "hot_score": 0,
                "source_match_score": base_source_match_score,
                "source_tags_matched": base_source_tags,
                "confidence_tier": "low",
                "gate_stage": "stage1_failed",
            }
            return False, stats, "stage1_spike_freshness"

        hot_score = 0
        if volume_m5 >= float(profile["min_volume_m5_usd"]):
            hot_score += 1
        if volume_m15 >= float(profile["min_volume_m15_usd"]):
            hot_score += 1
        if tx_count_m5 >= int(profile["min_tx_count_m5"]):
            hot_score += 1
        if liquidity_usd >= float(profile["min_liquidity_usd"]):
            hot_score += 1
        if age_minutes <= float(profile["max_pool_age_minutes"]):
            hot_score += 1
        if spike_ratio >= 0.45:
            hot_score += 1
        if tx_count_m1 >= min_tx_count_m1:
            hot_score += 1

        confidence_tier = "low"
        if hot_score >= int(profile["min_hot_points"]) + 2:
            confidence_tier = "high"
        elif hot_score >= int(profile["min_hot_points"]):
            confidence_tier = "medium"

        stats = {
            "volume": {"m1": volume_m1, "m5": volume_m5, "m15": volume_m15},
            "transactions": {"m1": tx_count_m1, "m5": tx_count_m5},
            "liquidity_usd": liquidity_usd,
            "pool_created_at": attrs.get("pool_created_at"),
            "pool_age_minutes": age_minutes,
            "spike_ratio": spike_ratio,
            "spike_ratio_m1_m5": spike_ratio_m1_m5,
            "hot_score": hot_score,
            "source_match_score": base_source_match_score,
            "source_tags_matched": base_source_tags,
            "confidence_tier": confidence_tier,
            "gate_stage": "stage2_passed",
        }
        if bool(profile["require_target_source"]) and base_source_match_score < 1:
            stats["gate_stage"] = "stage3_failed"
            return False, stats, "stage3_source_validation"
        if hot_score < int(profile["min_hot_points"]):
            stats["gate_stage"] = "stage2_failed"
            return False, stats, "stage2_velocity_liquidity"
        return True, stats, "pass"

    def _should_process_hot_pool(self, pool_id: str, stats: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc)
        last_processed_at = self._pool_processed_at.get(pool_id)
        current_hot_score = int(stats.get("hot_score") or 0)
        current_volume_m5 = float((stats.get("volume") or {}).get("m5") or 0.0)
        if not last_processed_at:
            self._pool_processed_at[pool_id] = now
            self._pool_last_hot_score[pool_id] = current_hot_score
            self._pool_last_volume_m5[pool_id] = current_volume_m5
            return True

        elapsed = (now - last_processed_at).total_seconds()
        prev_hot_score = self._pool_last_hot_score.get(pool_id, 0)
        prev_volume_m5 = self._pool_last_volume_m5.get(pool_id, 0.0)
        significant_jump = (
            current_hot_score >= prev_hot_score + 2
            or (prev_volume_m5 > 0 and current_volume_m5 >= prev_volume_m5 * 1.6)
        )
        if elapsed >= self._pool_reprocess_cooldown_seconds or significant_jump:
            self._pool_processed_at[pool_id] = now
            self._pool_last_hot_score[pool_id] = current_hot_score
            self._pool_last_volume_m5[pool_id] = current_volume_m5
            return True
        return False

    def _build_context_url(self, network: str, attrs: dict[str, Any]) -> str:
        address = attrs.get("address") or ""
        return f"https://www.geckoterminal.com/{network}/pools/{address}"

    def _build_text(self, network: str, token_name: str, token_symbol: str, stats: dict[str, Any]) -> str:
        volume_m5 = stats["volume"]["m5"]
        volume_m15 = stats["volume"]["m15"]
        tx_m5 = stats["transactions"]["m5"]
        liq = stats["liquidity_usd"]
        return (
            f"New launch pool detected on {network.upper()}: {token_name} ({token_symbol}). "
            f"Volume m5=${volume_m5:.2f}, m15=${volume_m15:.2f}, tx_m5={tx_m5}, liquidity=${liq:.2f}."
        )

    async def _poll_and_process(self) -> None:
        if self._circuit_open_until and datetime.now(timezone.utc) < self._circuit_open_until:
            logger.warning("Gecko detector circuit-open until %s", self._circuit_open_until.isoformat())
            return
        if self._degraded_until and datetime.now(timezone.utc) < self._degraded_until:
            logger.warning(
                "Gecko detector in degraded mode until %s (multiplier=%.2f)",
                self._degraded_until.isoformat(),
                self._adaptive_interval_multiplier,
            )
            return
        started = perf_counter()
        logger.debug("Polling GeckoTerminal new pools for networks: %s", ",".join(self.networks))
        client = self._http_client or httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout_seconds))
        processed_count = 0
        skipped_by_gate = 0
        skipped_by_cooldown = 0
        for network in self.networks:
            try:
                pools, included = await self._poll_network(client, network)
            except httpx.HTTPError as exc:
                self._on_poll_failure()
                logger.error("HTTP error polling GeckoTerminal (%s): %s", network, exc)
                continue

            token_index = self._build_token_index(included)
            logger.info("Fetched %s new pools from GeckoTerminal network=%s", len(pools), network)
            process_tasks: list[asyncio.Task[None]] = []
            for pool in pools:
                attrs = pool.get("attributes") or {}
                pool_address = str(attrs.get("address") or "")
                if not pool_address:
                    continue

                pool_id = f"{network}:{pool_address}"

                is_hot, stats, skip_reason = self._evaluate_pool(network, attrs)
                if not is_hot:
                    skipped_by_gate += 1
                    logger.debug("Skipping pool %s (%s)", pool_id, skip_reason)
                    continue
                if not self._should_process_hot_pool(pool_id, stats):
                    skipped_by_cooldown += 1
                    continue

                self._seen_pool_ids.append(pool_id)

                token_data = self._extract_base_token(pool, token_index)
                token_name = str(token_data.get("name") or attrs.get("name") or "Unknown").strip()
                token_symbol = str(token_data.get("symbol") or "???").strip().upper()
                context_url = self._build_context_url(network, attrs)

                payload = {
                    "id": pool_id,
                    "text": self._build_text(network, token_name, token_symbol, stats),
                    "author": "geckoterminal",
                    "timestamp": attrs.get("pool_created_at"),
                    "token_data": token_data,
                    "network": network,
                    "dex": attrs.get("dex_id"),
                    "volume": stats["volume"],
                    "transactions": stats["transactions"],
                    "liquidity_usd": stats["liquidity_usd"],
                    "pool_created_at": stats["pool_created_at"],
                    "spike_ratio": stats["spike_ratio"],
                    "spike_ratio_m1_m5": stats["spike_ratio_m1_m5"],
                    "hot_score": stats["hot_score"],
                    "confidence_tier": stats["confidence_tier"],
                    "gate_stage": stats["gate_stage"],
                    "source_match_score": stats["source_match_score"],
                    "source_tags_matched": stats["source_tags_matched"],
                }
                process_tasks.append(asyncio.create_task(self._process_payload_with_semaphore(payload, context_url)))
            if process_tasks:
                await asyncio.gather(*process_tasks)
                processed_count += len(process_tasks)

        self._last_poll_time = datetime.now(timezone.utc)
        logger.info(
            "gecko.loop_ms=%d processed=%d skip_gate=%d skip_cooldown=%d networks=%d rpm_mult=%.2f",
            int((perf_counter() - started) * 1000),
            processed_count,
            skipped_by_gate,
            skipped_by_cooldown,
            len(self.networks),
            self._adaptive_interval_multiplier,
        )

    async def process_payload(self, payload: dict[str, Any], context_url: str) -> None:
        try:
            candidate = normalize_gecko_payload(payload, context_url)
            loop = asyncio.get_running_loop()
            scored = await loop.run_in_executor(
                self._pipeline_executor,
                process_candidate,
                self.db,
                candidate,
            )

            if scored.decision in ("review", "priority_review"):
                logger.info("Candidate %s scored %s -> %s", candidate.id, scored.score, scored.decision)
                if self._telegram_worker:
                    self._schedule_review_notification(
                        candidate.id,
                        scored.review_priority,
                        scored.score,
                        scored.reason_codes,
                    )
                else:
                    logger.warning("Telegram worker not set, cannot send notification")
            else:
                logger.debug("Candidate %s skipped: %s", candidate.id, scored.reason_codes)
        except Exception as exc:
            logger.error("Error processing Gecko payload: %s", exc, exc_info=True)

    async def _process_payload_with_semaphore(self, payload: dict[str, Any], context_url: str) -> None:
        async with self._process_semaphore:
            await self.process_payload(payload, context_url)

    def _schedule_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> None:
        task = asyncio.create_task(
            self._send_review_notification_with_semaphore(
                candidate_id,
                review_priority,
                score,
                reason_codes,
            )
        )
        self._notification_tasks.add(task)
        task.add_done_callback(lambda t: self._notification_tasks.discard(t))

    async def _send_review_notification_with_semaphore(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> None:
        if not self._telegram_worker:
            return
        async with self._notify_semaphore:
            try:
                await self._telegram_worker.send_review_notification(
                    candidate_id,
                    review_priority,
                    score,
                    reason_codes,
                )
            except Exception as exc:
                logger.error("Failed to send Gecko review notification for %s: %s", candidate_id, exc, exc_info=True)
