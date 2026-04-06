"""Farcaster detector worker for polling and processing Farcaster signals."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

import httpx

from clankandclaw.config import StealthConfig
from clankandclaw.core.detectors.farcaster_detector import normalize_farcaster_event
from clankandclaw.utils.llm import enrich_signal_with_llm
from clankandclaw.core.pipeline import process_candidate, should_perform_ai_enrichment
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.utils.stealth_client import StealthClient

logger = logging.getLogger(__name__)


class FarcasterDetectorWorker:
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
        self.db = db
        self.poll_interval = poll_interval
        self.api_url = api_url
        self.api_key = api_key or ""
        self.max_results = max_results
        self.target_handles = [h.lower().lstrip("@") for h in (target_handles or ["bankr", "clanker"])]
        self.query_terms = query_terms or ["deploy", "launch", "contract", "ca", "token"]
        self.request_timeout_seconds = request_timeout_seconds
        self.max_requests_per_minute = max(1, max_requests_per_minute)
        self.max_process_concurrency = max(1, max_process_concurrency)
        self.max_query_concurrency = max(1, max_query_concurrency)
        self.loop_timeout_seconds = max(10.0, loop_timeout_seconds)
        self.candidate_process_timeout_seconds = max(1.0, candidate_process_timeout_seconds)
        self.max_pending_notifications = max(10, max_pending_notifications)
        self._stealth_config = stealth_config or StealthConfig()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None
        self._last_poll_time: datetime | None = None
        self._seen_cast_ids: deque[str] = deque()
        self._seen_cast_id_set: set[str] = set()
        self._max_seen_cast_ids = 5000
        self._billing_blocked = False
        self._stealth: StealthClient | None = None
        self._process_semaphore = asyncio.Semaphore(self.max_process_concurrency)
        self._query_semaphore = asyncio.Semaphore(self.max_query_concurrency)
        self._notify_semaphore = asyncio.Semaphore(8)
        self._notification_tasks: set[asyncio.Task[Any]] = set()
        self._pipeline_executor = ThreadPoolExecutor(
            max_workers=self.max_process_concurrency,
            thread_name_prefix="xcc-farcaster-pipeline",
        )
        self._last_request_at: datetime | None = None
        self._request_interval_multiplier = 1.0
        self._provider_cooldown_until: datetime | None = None
        self._consecutive_request_failures = 0

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        if self._running:
            logger.warning("Farcaster detector worker already running")
            return
        if not self.api_key:
            logger.warning("Farcaster detector started without NEYNAR_API_KEY; polling may fail")
        self._running = True
        self._last_poll_time = datetime.now(timezone.utc)
        self._stealth = StealthClient(self._stealth_config, timeout=self.request_timeout_seconds)
        self._task = asyncio.create_task(self._run())
        logger.info("Farcaster detector worker started")

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
            await asyncio.gather(*list(self._notification_tasks), return_exceptions=True)
        await asyncio.to_thread(self._pipeline_executor.shutdown, True)
        logger.info("Farcaster detector worker stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(self._poll_and_process(), timeout=self.loop_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error("Farcaster detector loop timeout after %.1fs", self.loop_timeout_seconds)
            except Exception as exc:
                logger.error("Error in Farcaster detector worker: %s", exc, exc_info=True)
            await asyncio.sleep(self.poll_interval)

    def _build_queries(self) -> list[str]:
        term_expr = " OR ".join(sorted(set(self.query_terms)))
        queries = [f"{handle} ({term_expr})" for handle in self.target_handles]
        seen: set[str] = set()
        deduped: list[str] = []
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            deduped.append(query)
        return deduped

    async def _poll_and_process(self) -> None:
        started = perf_counter()
        if self._provider_cooldown_until and datetime.now(timezone.utc) < self._provider_cooldown_until:
            logger.warning("Farcaster detector cooldown active until %s", self._provider_cooldown_until.isoformat())
            return
        if self._billing_blocked:
            logger.warning("Farcaster detector polling paused: Neynar cast search requires paid plan")
            return

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

        self._last_poll_time = datetime.now(timezone.utc)
        logger.info(
            "farcaster.loop_ms=%d processed=%d rpm_mult=%.2f",
            int((perf_counter() - started) * 1000),
            processed_count,
            self._request_interval_multiplier,
        )

    async def _respect_rate_limit(self, stealth: StealthClient) -> None:
        min_interval = (60.0 / float(self.max_requests_per_minute)) * self._request_interval_multiplier
        if not self._last_request_at:
            return
        elapsed = (datetime.now(timezone.utc) - self._last_request_at).total_seconds()
        if elapsed < min_interval:
            await stealth.sleep_jitter(min_interval - elapsed)

    def _on_request_success(self) -> None:
        self._consecutive_request_failures = 0
        self._request_interval_multiplier = max(1.0, self._request_interval_multiplier * 0.95)
        if self._provider_cooldown_until and datetime.now(timezone.utc) >= self._provider_cooldown_until:
            self._provider_cooldown_until = None

    def _on_request_failure(self, status_code: int | None = None) -> None:
        self._consecutive_request_failures += 1
        self._request_interval_multiplier = min(3.0, self._request_interval_multiplier * 1.2)
        if status_code in {403, 429}:
            self._provider_cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=90)
        elif self._consecutive_request_failures >= 6:
            self._provider_cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=120)

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
    async def process_event(self, event: dict[str, Any], context_url: str) -> None:
        try:
            candidate = normalize_farcaster_event(event, context_url)
            
            # Optimal: Only enrich via LLM if Gatekeeper allows
            if should_perform_ai_enrichment(candidate):
                enrichment = await enrich_signal_with_llm(candidate.raw_text)
                if enrichment:
                    # Update metadata with AI Insights
                    candidate.metadata.update({
                        "ai_enriched": True,
                        "ai_bullish_score": enrichment.get("bullish_score"),
                        "ai_rationale": enrichment.get("ai_rationale"),
                        "ai_description": enrichment.get("suggested_description"),
                        "ai_is_genuine": enrichment.get("is_genuine_launch"),
                    })
                    # Override suggested name/symbol if AI found better ones
                    if enrichment.get("name"):
                         candidate.suggested_name = enrichment["name"]
                    if enrichment.get("symbol"):
                         candidate.suggested_symbol = enrichment["symbol"]

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
            logger.error("Error processing Farcaster event: %s", exc, exc_info=True)

    async def _process_event_with_semaphore(self, event: dict[str, Any], context_url: str) -> None:
        async with self._process_semaphore:
            await self.process_event(event, context_url)

    async def _run_query(
        self,
        stealth: StealthClient,
        api_headers: dict[str, str],
        query: str,
    ) -> int:
        async with self._query_semaphore:
            params = {"q": query, "limit": self.max_results}
            response = await self._request_with_retry(stealth, api_headers, params)
            if response.status_code == 402:
                self._billing_blocked = True
                logger.error("Neynar API returned 402 PaymentRequired; disable farcaster_detector or upgrade plan")
                return 0
            response.raise_for_status()

            payload = response.json()
            casts = payload.get("result", {}).get("casts", payload.get("casts", []))
            logger.info("Fetched %s Farcaster casts for query '%s'", len(casts), query)

            process_tasks: list[asyncio.Task[None]] = []
            for cast in casts:
                cast_id = str(cast.get("hash") or cast.get("id") or "")
                if not cast_id or not self._mark_cast_seen(cast_id):
                    continue

                author = cast.get("author") or {}
                text = str(cast.get("text") or "")
                reactions = cast.get("reactions") or {}
                replies = cast.get("replies") or {}
                mentions = cast.get("mentioned_profiles") or cast.get("mentions") or []
                mentioned_handles = []
                for mention in mentions:
                    if isinstance(mention, dict):
                        username = mention.get("username")
                        if username:
                            mentioned_handles.append(str(username))

                event = {
                    "id": cast_id,
                    "text": text,
                    "author": {"username": author.get("username")},
                    "created_at": cast.get("timestamp"),
                    "mentioned_handles": mentioned_handles,
                    "like_count": int(reactions.get("likes_count") or 0),
                    "recast_count": int(reactions.get("recasts_count") or 0),
                    "reply_count": int(replies.get("count") or 0),
                }
                context_url = str(cast.get("permalink") or f"https://warpcast.com/~/conversations/{cast_id}")
                process_tasks.append(asyncio.create_task(self._process_event_with_semaphore(event, context_url)))

            if process_tasks:
                await asyncio.gather(*process_tasks)
            return len(process_tasks)

    def _mark_cast_seen(self, cast_id: str) -> bool:
        if cast_id in self._seen_cast_id_set:
            return False
        self._seen_cast_id_set.add(cast_id)
        self._seen_cast_ids.append(cast_id)
        if len(self._seen_cast_ids) > self._max_seen_cast_ids:
            oldest = self._seen_cast_ids.popleft()
            self._seen_cast_id_set.discard(oldest)
        return True

    def _schedule_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> None:
        if len(self._notification_tasks) >= self.max_pending_notifications:
            logger.warning(
                "Skipping Farcaster review notification for %s: pending queue saturated (%d)",
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
                logger.error("Failed to send review notification for %s: %s", candidate_id, exc, exc_info=True)
