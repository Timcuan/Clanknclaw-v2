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
from clankandclaw.utils.llm import validate_gecko_candidate_with_llm
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.utils.stealth_client import StealthClient

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
        min_volume_m5_usd: float = 500.0,
        min_volume_m15_usd: float = 1500.0,
        min_tx_count_m5: int = 5,
        min_liquidity_usd: float = 2000.0,
        max_requests_per_minute: int = 40,
        request_timeout_seconds: float = 20.0,
        base_target_sources: list[str] | None = None,
        max_process_concurrency: int = 10,
        loop_timeout_seconds: float = 90.0,
        candidate_process_timeout_seconds: float = 20.0,
        max_pending_notifications: int = 500,
        stealth_config: StealthConfig | None = None,
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
        self.loop_timeout_seconds = max(10.0, loop_timeout_seconds)
        self.candidate_process_timeout_seconds = max(1.0, candidate_process_timeout_seconds)
        self.max_pending_notifications = max(10, max_pending_notifications)
        if base_target_sources is not None:
             self.base_target_sources = [_normalize_tag(s) for s in base_target_sources if s]
        else:
             self.base_target_sources = [_normalize_tag(s) for s in ["bankr", "doppler", "zora", "virtual", "uniswapv4", "clanker"]]
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None
        self._last_poll_time: datetime | None = None
        self._seen_pool_ids: deque[str] = deque(maxlen=5000)
        self._pool_processed_at: dict[str, datetime] = {}
        self._pool_last_hot_score: dict[str, int] = {}
        self._pool_last_volume_m5: dict[str, float] = {}
        self._pool_last_notified_at: dict[str, datetime] = {}
        self._stealth_config = stealth_config or StealthConfig()
        self._last_request_at: datetime | None = None
        self._stealth: StealthClient | None = None
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

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        if self._running:
            logger.warning("Gecko detector worker already running")
            return

        self._running = True
        self._last_poll_time = datetime.now(timezone.utc)
        self._stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
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
        if self._stealth:
            await self._stealth.aclose()
            self._stealth = None
        if self._notification_tasks:
            for t in list(self._notification_tasks):
                t.cancel()
            await asyncio.gather(*list(self._notification_tasks), return_exceptions=True)
        await asyncio.to_thread(self._pipeline_executor.shutdown, wait=False, cancel_futures=True)
        logger.info("Gecko detector worker stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(self._poll_and_process(), timeout=self.loop_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error("Gecko detector loop timeout after %.1fs", self.loop_timeout_seconds)
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

    async def _respect_rate_limit(self, stealth: StealthClient) -> None:
        min_interval = self._base_request_interval_seconds * self._adaptive_interval_multiplier
        if not self._last_request_at:
            return
        elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
        if elapsed < min_interval:
            await stealth.sleep_jitter(min_interval - elapsed)

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

    async def _poll_network(self, stealth: StealthClient, network: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        profile = self._profile_for_network(network)
        mode = profile.get("scan_mode", "new_pools")
        url = f"{self.api_base_url}/networks/{network}/{mode}"
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
        """Extract base token attributes, identifying the candidate token (not the native/quote one)."""
        rel = (pool.get("relationships") or {}).get("base_token") or {}
        token_data = rel.get("data") or {}
        token_id = str(token_data.get("id") or "")
        
        if token_id and token_id in token_index:
            return token_index[token_id]
            
        # Fallback: if not in index, look for any token in pool attributes that isn't WETH/SOL
        attrs = pool.get("attributes") or {}
        pname = str(attrs.get("name") or "").lower()
        if " / " in pname:
            parts = pname.split(" / ")
            # If we know the network, we might know the common quote token name
            # but for now, we just return {} if relationship lookup failed
            
        return {}

    def _profile_for_network(self, network: str, scan_mode: str | None = None) -> dict[str, float]:
        """Return the filter profile for the given network and optional scan mode override."""
        profiles: dict[str, dict[str, Any]] = {
            # Base: New Launch — catch early momentum in first 60 minutes
            "base_new_launch": {
                "min_volume_m5_usd": 200.0,
                "min_volume_m15_usd": 600.0,
                "min_tx_count_m5": 3.0,
                "min_liquidity_usd": 500.0,
                "max_pool_age_minutes": 60.0,
                "min_hot_points": 2.0,
                "require_target_source": 0.0,
                "scan_mode": "new_pools",
                "check_buy_ratio": 1.0,
                "signal_tag": "gecko_new_launch",
            },
            # Base: Volume Momentum — trending pools with active volume regardless of age
            "base_trending": {
                "min_volume_m5_usd": 2000.0,
                "min_volume_m15_usd": 5000.0,
                "min_tx_count_m5": 10.0,
                "min_liquidity_usd": 3000.0,
                "max_pool_age_minutes": 1440.0,
                "min_hot_points": 3.0,
                "require_target_source": 0.0,
                "scan_mode": "trending_pools",
                "check_buy_ratio": 1.0,
                "min_spike_ratio": 0.3,
                "signal_tag": "gecko_trending",
            },
            "solana": {
                "min_volume_h1_usd": 100000.0, "min_tx_count_h1": 200.0, "min_liquidity_usd": 35000.0,
                "max_pool_age_minutes": 1440.0, "min_hot_points": 3.0, "require_target_source": 0.0,
                "scan_mode": "trending_pools",
                "required_dex_ids": ["raydium", "raydium-clmm", "meteora", "orca", "fluxbeam", "lifinity-v2"],
            },
            "bsc": {
                "min_volume_h1_usd": 80000.0, "min_tx_count_h1": 150.0, "min_liquidity_usd": 25000.0,
                "max_pool_age_minutes": 1440.0, "min_hot_points": 3.0, "require_target_source": 0.0,
                "scan_mode": "trending_pools",
                "required_dex_ids": ["pancakeswap_v2", "pancakeswap_v3", "pancakeswap_v4", "uniswap_v3"],
            },
            "eth": {
                "min_volume_m5_usd": 1000.0, "min_volume_m15_usd": 3000.0, "min_tx_count_m5": 8.0,
                "min_liquidity_usd": 5000.0, "max_pool_age_minutes": 90.0, "min_hot_points": 3.0,
                "require_target_source": 1.0, "scan_mode": "new_pools",
            },
        }
        # Select correct Base sub-profile based on scan_mode
        if network == "base":
            if scan_mode == "trending_pools":
                return profiles["base_trending"]
            return profiles["base_new_launch"]
        defaults = {
            "min_volume_m5_usd": self.min_volume_m5_usd,
            "min_volume_m15_usd": self.min_volume_m15_usd,
            "min_tx_count_m5": float(self.min_tx_count_m5),
            "min_liquidity_usd": self.min_liquidity_usd,
            "max_pool_age_minutes": float(self.max_pool_age_minutes),
            "min_hot_points": 3.0,
            "require_target_source": 0.0,
        }
        defaults.update(profiles.get(network, {}))
        return defaults

    def _base_source_match(self, attrs: dict[str, Any]) -> tuple[int, list[str]]:
        haystack_values = [
            str(attrs.get("dex_id") or ""),
            str(attrs.get("name") or ""),
        ]
        haystack = " ".join(_normalize_tag(v) for v in haystack_values if v)
        matched = [tag for tag in self.base_target_sources if tag and tag in haystack]
        return len(set(matched)), sorted(set(matched))

    def _evaluate_pool(self, network: str, attrs: dict[str, Any], scan_mode: str | None = None) -> tuple[bool, dict[str, Any], str]:
        profile = self._profile_for_network(network, scan_mode)
        volume = attrs.get("volume_usd") or {}
        tx_data = attrs.get("transactions") or {}
        tx_m1 = tx_data.get("m1") or {}
        tx_m5 = tx_data.get("m5") or {}

        volume_m1 = _to_float(volume.get("m1"))
        volume_m5 = _to_float(volume.get("m5"))
        volume_m15 = _to_float(volume.get("m15"))
        volume_h1 = _to_float(volume.get("h1"))

        # Momentum ratios
        spike_ratio_m1_m5 = (volume_m1 / volume_m5) if volume_m5 > 0 else 0.0
        spike_ratio = (volume_m5 / volume_m15) if volume_m15 > 0 else 0.0

        buy_count_m5 = _to_int(tx_m5.get("buys"))
        sell_count_m5 = _to_int(tx_m5.get("sells"))
        tx_count_m1 = _to_int(tx_m1.get("buys")) + _to_int(tx_m1.get("sells"))
        tx_count_m5 = buy_count_m5 + sell_count_m5
        tx_count_h1 = _to_int(tx_data.get("h1", {}).get("buys")) + _to_int(tx_data.get("h1", {}).get("sells"))
        buy_ratio_m5 = buy_count_m5 / tx_count_m5 if tx_count_m5 > 0 else 0.0

        liquidity_usd = _to_float(attrs.get("reserve_in_usd"))
        age_minutes = _pool_age_minutes(attrs.get("pool_created_at"))
        dex_id = str(attrs.get("dex_id") or "").lower()

        actual_scan_mode = str(profile.get("scan_mode", "new_pools"))
        is_trending_mode = actual_scan_mode == "trending_pools"

        base_source_match_score, base_source_tags = self._base_source_match(attrs)

        min_volume_m1 = max(200.0, float(profile.get("min_volume_m5_usd") or 1.0) * 0.2)
        min_tx_count_m1 = max(2, int(float(profile.get("min_tx_count_m5") or 1.0) * 0.4))
        has_m1_signal = volume_m1 > 0 or tx_count_m1 > 0

        # Stage 1: Gate logic differs by mode
        if is_trending_mode:
            # Volume Momentum Gate: recent m5 volume must be significant
            stage1_fast_spike = (
                volume_m5 >= float(profile.get("min_volume_m5_usd", 1.0))
                and tx_count_m5 >= int(float(profile.get("min_tx_count_m5", 1.0)))
                and liquidity_usd >= float(profile["min_liquidity_usd"])
                and age_minutes <= float(profile["max_pool_age_minutes"])
            )
        else:
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

        # Buy Ratio Filter: reject if mostly sells (bot dump / no genuine demand)
        if profile.get("check_buy_ratio") and tx_count_m5 >= 3 and buy_ratio_m5 < 0.4:
            stage1_fast_spike = False

        # Spike ratio gate for trending mode
        if is_trending_mode and float(profile.get("min_spike_ratio", 0.0)) > 0:
            if spike_ratio < float(profile["min_spike_ratio"]):
                stage1_fast_spike = False

        # DEX whitelist gate
        required_dex_ids = profile.get("required_dex_ids")
        if required_dex_ids and dex_id not in required_dex_ids:
            stage1_fast_spike = False

        if float(profile.get("require_target_source", 0.0)) > 0 and base_source_match_score <= 0:
            stage1_fast_spike = False

        # Anti-Wash Filter
        if not is_trending_mode and liquidity_usd > 0 and volume_m5 / liquidity_usd > 2.5:
            stage1_fast_spike = False

        # Bot-Pump Filter
        if not is_trending_mode and spike_ratio_m1_m5 > 0.8:
            stage1_fast_spike = False
             
        if not stage1_fast_spike:
            stats = {
                "dex_id": dex_id,
                "volume": {"m1": volume_m1, "m5": volume_m5, "m15": volume_m15, "h1": volume_h1},
                "transactions": {"m1": tx_count_m1, "m5": tx_count_m5, "h1": tx_count_h1},
                "liquidity_usd": liquidity_usd,
                "pool_created_at": attrs.get("pool_created_at"),
                "pool_age_minutes": age_minutes,
                "spike_ratio": spike_ratio,
                "spike_ratio_m1_m5": spike_ratio_m1_m5,
                "buy_ratio_m5": buy_ratio_m5,
                "hot_score": 0,
                "source_match_score": base_source_match_score,
                "source_tags_matched": base_source_tags,
                "confidence_tier": "low",
                "gate_stage": "stage1_failed",
                "signal_tag": str(profile.get("signal_tag", "")),
            }
            return False, stats, "stage1_spike_freshness"

        hot_score = 0
        reason_signals: list[str] = []

        # Add the mode-specific tag first
        signal_tag = str(profile.get("signal_tag", ""))
        if signal_tag:
            reason_signals.append(signal_tag)

        if is_trending_mode:
            if volume_m5 >= float(profile["min_volume_m5_usd"]):
                hot_score += 2
                reason_signals.append("gecko_volume_surge")
            if tx_count_m5 >= int(float(profile["min_tx_count_m5"])):
                hot_score += 1
                reason_signals.append("gecko_tx_m5_ok")
            if liquidity_usd >= float(profile["min_liquidity_usd"]):
                hot_score += 1
            if spike_ratio >= 0.45:
                hot_score += 1
                reason_signals.append("gecko_spike_ratio_strong")
            elif spike_ratio >= 0.3:
                reason_signals.append("gecko_spike_ratio_ok")
        else:
            if volume_m5 >= float(profile["min_volume_m5_usd"]):
                hot_score += 1
            if volume_m15 >= float(profile["min_volume_m15_usd"]):
                hot_score += 1
            if tx_count_m5 >= int(profile["min_tx_count_m5"]):
                hot_score += 1
                reason_signals.append("gecko_tx_m5_ok")
            if liquidity_usd >= float(profile["min_liquidity_usd"]):
                hot_score += 1
            if age_minutes <= float(profile["max_pool_age_minutes"]):
                hot_score += 1
            if spike_ratio >= 0.45:
                hot_score += 1
                reason_signals.append("gecko_spike_ratio_strong")
            elif spike_ratio >= 0.25:
                reason_signals.append("gecko_spike_ratio_ok")
            if tx_count_m1 >= min_tx_count_m1:
                hot_score += 1

        # Buy Pressure Bonus
        if buy_ratio_m5 >= 0.6 and volume_m5 >= 150:
            hot_score += 1
            reason_signals.append("gecko_buy_pressure")

        # Fresh launch bonus
        if age_minutes <= 15 and tx_count_m5 >= 2:
            hot_score += 1
            reason_signals.append("gecko_fresh_launch")

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
            "buy_ratio_m5": buy_ratio_m5,
            "hot_score": hot_score,
            "source_match_score": base_source_match_score,
            "source_tags_matched": base_source_tags,
            "confidence_tier": confidence_tier,
            "gate_stage": "stage2_passed",
            "reason_signals": reason_signals,
            "signal_tag": signal_tag,
        }
        if bool(profile["require_target_source"]) and base_source_match_score < 1:
            stats["gate_stage"] = "stage3_failed"
            return False, stats, "stage3_source_validation"
        if hot_score < int(profile["min_hot_points"]):
            stats["gate_stage"] = "stage2_failed"
            return False, stats, "stage2_velocity_liquidity"
        return True, stats, "pass"

    def _evict_stale_pool_state(self) -> None:
        """Remove pool state entries older than 2× the reprocess cooldown to prevent unbounded growth."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._pool_reprocess_cooldown_seconds * 2)
        stale = [pid for pid, ts in self._pool_processed_at.items() if ts < cutoff]
        for pid in stale:
            self._pool_processed_at.pop(pid, None)
            self._pool_last_hot_score.pop(pid, None)
            self._pool_last_volume_m5.pop(pid, None)
        if stale:
            logger.debug("Evicted %d stale pool state entries", len(stale))

    def _should_process_hot_pool(self, pool_id: str, stats: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc)
        last_processed_at = self._pool_processed_at.get(pool_id)
        last_notified_at = self._pool_last_notified_at.get(pool_id)
        current_hot_score = int(stats.get("hot_score") or 0)
        current_volume_m5 = float((stats.get("volume") or {}).get("m5") or 0.0)
        
        if not last_processed_at:
            self._pool_processed_at[pool_id] = now
            self._pool_last_hot_score[pool_id] = current_hot_score
            self._pool_last_volume_m5[pool_id] = current_volume_m5
            self._pool_last_notified_at[pool_id] = now
            return True

        elapsed = (now - last_processed_at).total_seconds()
        
        # Mandatory notification cooldown: 15 minutes
        notification_elapsed = (now - (last_notified_at or now - timedelta(days=1))).total_seconds()
        if notification_elapsed < 900:  # 15 minutes
             return False

        prev_hot_score = self._pool_last_hot_score.get(pool_id, 0)
        prev_volume_m5 = self._pool_last_volume_m5.get(pool_id, 0.0)
        
        # Significant jump: Score +4 OR Volume 3.0x
        significant_jump = (
            current_hot_score >= prev_hot_score + 4
            or (prev_volume_m5 > 0 and current_volume_m5 >= prev_volume_m5 * 3.0)
        )
        
        if elapsed >= self._pool_reprocess_cooldown_seconds or significant_jump:
            self._pool_processed_at[pool_id] = now
            self._pool_last_hot_score[pool_id] = current_hot_score
            self._pool_last_volume_m5[pool_id] = current_volume_m5
            self._pool_last_notified_at[pool_id] = now
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
        stealth = self._stealth
        if stealth is None:
            stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
        processed_count = 0
        skipped_by_gate = 0
        skipped_by_cooldown = 0
        # Collect all process tasks across all networks before awaiting any of them,
        # so network fetching isn't blocked by pipeline processing of the previous network.
        all_process_tasks: list[asyncio.Task[None]] = []
        # Build poll targets: Base gets polled twice (new_pools + trending_pools)
        poll_targets: list[tuple[str, str | None]] = []
        for network in self.networks:
            if network == "base":
                poll_targets.append(("base", "new_pools"))      # New Launch mode
                poll_targets.append(("base", "trending_pools"))  # Volume Momentum mode
            else:
                poll_targets.append((network, None))  # use profile default

        for network, mode_override in poll_targets:
            try:
                # Temporarily override scan_mode for this poll target
                profile = self._profile_for_network(network, mode_override)
                actual_mode = str(profile.get("scan_mode", "new_pools"))
                url = f"{self.api_base_url}/networks/{network}/{actual_mode}"
                await self._respect_rate_limit(stealth)
                response = await stealth.get(url, params={"page": 1})
                self._last_request_at = datetime.now(timezone.utc)
                stealth.on_response(response.status_code)
                if not response.is_success:
                    self._on_poll_failure()
                    logger.warning("GeckoTerminal poll failed network=%s mode=%s status=%d", network, actual_mode, response.status_code)
                    continue
                self._on_poll_success()
                payload_data = response.json()
                pools = list(payload_data.get("data", [])[:self.max_results])
                included = payload_data.get("included", [])
            except httpx.HTTPError as exc:
                self._on_poll_failure()
                logger.error("HTTP error polling GeckoTerminal (%s/%s): %s", network, mode_override, exc)
                continue

            token_index = self._build_token_index(included)
            logger.info("Fetched %d pools network=%s mode=%s", len(pools), network, mode_override or "default")

            for pool in pools:
                attrs = pool.get("attributes") or {}
                pool_address = str(attrs.get("address") or "")
                if not pool_address:
                    continue

                # Stable ID format used across pipeline/tests.
                pool_id = f"{network}:{pool_address}"

                is_hot, stats, skip_reason = self._evaluate_pool(network, attrs, scan_mode=mode_override)
                if not is_hot:
                    skipped_by_gate += 1
                    logger.debug("Skipping pool %s (%s)", pool_id, skip_reason)
                    continue
                if not self._should_process_hot_pool(pool_id, stats):
                    skipped_by_cooldown += 1
                    continue

                self._seen_pool_ids.append(pool_id)

                token_data = self._extract_base_token(pool, token_index)

                raw_name = (token_data.get("name") or attrs.get("name") or "").strip()
                token_symbol = (token_data.get("symbol") or "").strip().upper()

                if " / " in raw_name:
                    token_name = raw_name.split(" / ")[0].strip()
                    if not token_symbol:
                        token_symbol = token_name[:10].upper()
                else:
                    token_name = raw_name or "Unknown"

                if not token_symbol:
                    token_symbol = "???"

                context_url = self._build_context_url(network, attrs)

                # Merge reason_signals from scoring with classic source tags
                reason_signals: list[str] = list(stats.get("reason_signals") or [])
                for tag in (stats.get("source_tags_matched") or []):
                    reason_signals.append(f"source_{tag}")

                pool_payload = {
                    "id": pool_id,
                    "text": self._build_text(network, token_name, token_symbol, stats),
                    "author": "geckoterminal",
                    "timestamp": attrs.get("pool_created_at"),
                    "token_data": token_data,
                    "token_name": token_name,
                    "token_symbol": token_symbol,
                    "network": network,
                    "scan_mode": mode_override or actual_mode,
                    "dex": attrs.get("dex_id"),
                    "volume": stats["volume"],
                    "transactions": stats["transactions"],
                    "liquidity_usd": stats["liquidity_usd"],
                    "pool_created_at": stats["pool_created_at"],
                    "pool_age_minutes": stats.get("pool_age_minutes", 999.0),
                    "spike_ratio": stats["spike_ratio"],
                    "spike_ratio_m1_m5": stats["spike_ratio_m1_m5"],
                    "buy_ratio_m5": stats.get("buy_ratio_m5", 0.0),
                    "hot_score": stats["hot_score"],
                    "confidence_tier": stats["confidence_tier"],
                    "gate_stage": stats["gate_stage"],
                    "source_match_score": stats["source_match_score"],
                    "source_tags_matched": stats["source_tags_matched"],
                    "reason_signals": reason_signals,
                }
                all_process_tasks.append(asyncio.create_task(self._process_payload_with_semaphore(pool_payload, context_url)))
        if all_process_tasks:
            await asyncio.gather(*all_process_tasks)
            processed_count = len(all_process_tasks)

        self._last_poll_time = datetime.now(timezone.utc)
        self._evict_stale_pool_state()
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
            scored = await asyncio.wait_for(
                loop.run_in_executor(
                    self._pipeline_executor,
                    process_candidate,
                    self.db,
                    candidate,
                ),
                timeout=self.candidate_process_timeout_seconds,
            )

            if scored.decision in ("review", "priority_review", "auto_deploy"):
                logger.info("Candidate %s scored %s -> %s", candidate.id, scored.score, scored.decision)

                # Build metadata from payload
                token_data = payload.get("token_data") or {}
                metadata = {
                    "network": payload.get("network", "unknown"),
                    "token_name": payload.get("token_name") or token_data.get("name") or "",
                    "token_symbol": payload.get("token_symbol") or token_data.get("symbol") or "",
                    "liquidity_usd": float(payload.get("liquidity_usd") or 0.0),
                    "volume": payload.get("volume") or {},
                    "transactions": payload.get("transactions") or {},
                    "confidence_tier": payload.get("confidence_tier", "high"),
                    "fee_type": payload.get("fee_type", "static"),
                    "scan_mode": payload.get("scan_mode", "new_pools"),
                    "buy_ratio_m5": float(payload.get("buy_ratio_m5") or 0.0),
                    "pool_age_minutes": float(payload.get("pool_age_minutes") or 999.0),
                    "dex": payload.get("dex", "unknown"),
                    "token_address": token_data.get("address"),
                    "fdv_usd": float(payload.get("fdv_usd") or 0.0),
                    "websites": payload.get("websites") or [],
                    "socials": payload.get("socials") or [],
                    "context_url": context_url,

                }

                # --- LLM Quality Gate (auto-deploy path only) ---
                # Only fires for candidates heading toward auto-deploy.
                # Non-blocking: 5s timeout, defaults to safe=True on failure.
                auto_threshold = 85  # Mirror ops.auto_threshold default
                try:
                    auto_threshold = int(
                        self.db.get_runtime_setting("ops.auto_threshold") or 85  # type: ignore[union-attr]
                    )
                except Exception:
                    pass

                is_auto_candidate = (
                    scored.decision == "auto_deploy"
                    or scored.score >= auto_threshold
                )

                if is_auto_candidate and metadata.get("token_name") and metadata.get("token_symbol"):
                    try:
                        llm_result = await validate_gecko_candidate_with_llm(
                            token_name=metadata["token_name"],
                            token_symbol=metadata["token_symbol"],
                            volume_m5=float((payload.get("volume") or {}).get("m5") or 0.0),
                            liquidity=float(payload.get("liquidity_usd") or 0.0),
                            age_minutes=float(payload.get("pool_age_minutes") or 999.0),
                            scan_mode=payload.get("scan_mode", "new_pools"),
                        )

                        if llm_result.get("description"):
                            metadata["ai_description"] = llm_result["description"]

                        if not llm_result.get("safe", True):
                            risk = llm_result.get("risk") or "flagged"
                            logger.warning(
                                "LLM flagged candidate %s as unsafe (risk=%s); downgrading to review",
                                candidate.id, risk,
                            )
                            metadata["llm_risk_flag"] = risk
                            # Downgrade: override decision to review, skip auto-trigger
                            from clankandclaw.models.token import ScoredCandidate
                            scored = ScoredCandidate(
                                candidate_id=scored.candidate_id,
                                score=scored.score,
                                decision="review",
                                reason_codes=scored.reason_codes + [f"llm_risk_{risk}"],
                                recommended_platform=scored.recommended_platform,
                                review_priority="review",
                                auto_trigger=False,
                            )
                    except Exception as exc:
                        logger.debug("LLM gate skipped for %s: %s", candidate.id, exc)

                # Merge detector signals with pipeline reason codes
                detector_signals: list[str] = list(payload.get("reason_signals") or [])
                pipeline_codes: list[str] = list(scored.reason_codes or [])
                seen: set[str] = set()
                merged_codes: list[str] = []
                for code in detector_signals + pipeline_codes:
                    if code not in seen:
                        seen.add(code)
                        merged_codes.append(code)

                if self._telegram_worker:
                    self._schedule_review_notification(
                        candidate.id,
                        scored.review_priority,
                        scored.score,
                        merged_codes,
                        context_url=context_url,
                        metadata=metadata,
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
        *,
        context_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if len(self._notification_tasks) >= self.max_pending_notifications:
            logger.warning(
                "Skipping Gecko review notification for %s: pending queue saturated (%d)",
                candidate_id,
                len(self._notification_tasks),
            )
            return
        task = asyncio.create_task(
            self._send_review_notification_with_semaphore(
                candidate_id,
                review_priority,
                score,
                reason_codes,
                context_url=context_url,
                metadata=metadata,
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
        *,
        context_url: str | None = None,
        metadata: dict[str, Any] | None = None,
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
                    context_url=context_url,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.error("Failed to send Gecko review notification for %s: %s", candidate_id, exc, exc_info=True)
