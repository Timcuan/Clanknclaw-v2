"""X detector worker for polling and processing X signals."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

from clankandclaw.core.detectors.x_detector import normalize_x_event
from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


class XDetectorWorker:
    """Worker that polls X for deploy signals and processes them through the pipeline."""

    def __init__(
        self,
        db: DatabaseManager,
        poll_interval: float = 30.0,
        keywords: list[str] | None = None,
        max_results: int = 20,
        target_handles: list[str] | None = None,
        query_terms: list[str] | None = None,
        max_process_concurrency: int = 8,
        max_query_concurrency: int = 3,
        loop_timeout_seconds: float = 90.0,
        candidate_process_timeout_seconds: float = 20.0,
        max_pending_notifications: int = 500,
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.keywords = keywords or ["deploy", "launch"]
        self.max_results = max_results
        self.target_handles = [h.lower().lstrip("@") for h in (target_handles or ["bankrbot", "clankerdeploy"])]
        self.query_terms = query_terms or ["deploy", "launch", "contract", "ca", "token"]
        self.max_process_concurrency = max(1, max_process_concurrency)
        self.max_query_concurrency = max(1, max_query_concurrency)
        self.loop_timeout_seconds = max(10.0, loop_timeout_seconds)
        self.candidate_process_timeout_seconds = max(1.0, candidate_process_timeout_seconds)
        self.max_pending_notifications = max(10, max_pending_notifications)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None  # Will be set by supervisor
        self._api: Any = None  # twscrape API instance
        self._last_poll_time: datetime | None = None
        self._seen_tweet_ids: deque[str] = deque(maxlen=5000)
        self._process_semaphore = asyncio.Semaphore(self.max_process_concurrency)
        self._query_semaphore = asyncio.Semaphore(self.max_query_concurrency)
        self._notify_semaphore = asyncio.Semaphore(8)
        self._notification_tasks: set[asyncio.Task[Any]] = set()
        self._pipeline_executor = ThreadPoolExecutor(
            max_workers=self.max_process_concurrency,
            thread_name_prefix="xcc-x-pipeline",
        )

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        """Set the telegram worker for sending notifications."""
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        """Start the X detector worker."""
        if self._running:
            logger.warning("X detector worker already running")
            return

        # Try to initialize twscrape
        try:
            from twscrape import API
            self._api = API()
            logger.info("twscrape initialized successfully")
        except ImportError:
            logger.warning("twscrape not installed, X detector will run in mock mode")
            self._api = None
        except Exception as exc:
            logger.warning(f"Failed to initialize twscrape: {exc}")
            self._api = None

        self._running = True
        self._last_poll_time = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run())
        logger.info("X detector worker started")

    async def stop(self) -> None:
        """Stop the X detector worker."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._notification_tasks:
            await asyncio.gather(*list(self._notification_tasks), return_exceptions=True)
        await asyncio.to_thread(self._pipeline_executor.shutdown, True)
        logger.info("X detector worker stopped")

    async def _run(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                await asyncio.wait_for(self._poll_and_process(), timeout=self.loop_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error("X detector loop timeout after %.1fs", self.loop_timeout_seconds)
            except Exception as exc:
                logger.error(f"Error in X detector worker: {exc}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def _poll_and_process(self) -> None:
        """Poll X for new signals and process them."""
        if not self._api:
            logger.debug("X detector polling skipped (twscrape not available)")
            return

        try:
            started = perf_counter()
            processed_count = 0
            queries = self._build_queries()
            query_tasks = [asyncio.create_task(self._run_query(query)) for query in queries]
            if query_tasks:
                results = await asyncio.gather(*query_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.error("X query task failed: %s", result, exc_info=True)
                        continue
                    processed_count += int(result)
                    
            self._last_poll_time = datetime.now(timezone.utc)
            logger.info("x.loop_ms=%d processed=%d queries=%d", int((perf_counter() - started) * 1000), processed_count, len(queries))
            
        except Exception as exc:
            logger.error(f"Error in X polling: {exc}", exc_info=True)

    def _build_queries(self) -> list[str]:
        term_expr = " OR ".join(sorted(set(self.query_terms + self.keywords)))
        queries: list[str] = []
        for handle in self.target_handles:
            queries.append(f"to:{handle} ({term_expr})")
            queries.append(f"from:{handle} ({term_expr})")
            queries.append(f"@{handle} ({term_expr})")
        # Keep one generic query for broader context discovery.
        queries.append(f"({' OR '.join(self.keywords)}) token")
        deduped: list[str] = []
        seen: set[str] = set()
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            deduped.append(query)
        return deduped

    def _extract_media_urls(self, tweet: Any) -> list[str]:
        urls: list[str] = []
        media = getattr(tweet, "media", None)
        if media is None:
            return []

        def _push_url(value: Any) -> None:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)

        for attr in ("url", "fullUrl", "previewUrl", "imageUrl", "mediaUrl"):
            _push_url(getattr(media, attr, None))

        for group in ("photos", "videos", "animated", "items"):
            items = getattr(media, group, None)
            if not items:
                continue
            for item in items:
                for attr in ("url", "fullUrl", "previewUrl", "imageUrl", "mediaUrl"):
                    _push_url(getattr(item, attr, None))
                    if isinstance(item, dict):
                        _push_url(item.get(attr))

        # De-dupe preserving order.
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    async def _run_query(self, query: str) -> int:
        async with self._query_semaphore:
            logger.debug("Searching X with query: %s", query)
            try:
                tweets = []
                async for tweet in self._api.search(
                    query,
                    limit=self.max_results,
                    kv={"product": "Latest"},
                ):
                    tweets.append(tweet)

                logger.info("Found %s tweets for query '%s'", len(tweets), query)
                process_tasks: list[asyncio.Task[None]] = []
                for tweet in tweets:
                    tweet_id = str(getattr(tweet, "id", ""))
                    if not tweet_id or tweet_id in self._seen_tweet_ids:
                        continue
                    self._seen_tweet_ids.append(tweet_id)

                    event = {
                        "id": tweet_id,
                        "text": getattr(tweet, "rawContent", "") or "",
                        "user": {
                            "username": getattr(getattr(tweet, "user", None), "username", "unknown"),
                        },
                        "created_at": getattr(tweet, "date", None).isoformat() if getattr(tweet, "date", None) else None,
                        "like_count": int(getattr(tweet, "likeCount", 0) or 0),
                        "retweet_count": int(getattr(tweet, "retweetCount", 0) or 0),
                        "reply_count": int(getattr(tweet, "replyCount", 0) or 0),
                        "quote_count": int(getattr(tweet, "quoteCount", 0) or 0),
                        "view_count": int(getattr(tweet, "viewCount", 0) or 0),
                        "conversation_id": str(getattr(tweet, "conversationId", "") or ""),
                        "in_reply_to_tweet_id": str(getattr(tweet, "inReplyToTweetId", "") or ""),
                        "mentioned_users": [
                            {"username": getattr(u, "username", "")}
                            for u in (getattr(tweet, "mentionedUsers", None) or [])
                            if getattr(u, "username", "")
                        ],
                        "media": [{"url": url} for url in self._extract_media_urls(tweet)],
                    }
                    context_url = f"https://x.com/{event['user']['username']}/status/{tweet.id}"
                    process_tasks.append(
                        asyncio.create_task(self._process_event_with_semaphore(event, context_url))
                    )
                if process_tasks:
                    await asyncio.gather(*process_tasks)
                return len(process_tasks)
            except Exception as exc:
                logger.error("Error searching X query '%s': %s", query, exc, exc_info=True)
                return 0

    async def process_event(self, event: dict[str, Any], context_url: str) -> None:
        """Process a single X event through the pipeline."""
        try:
            candidate = normalize_x_event(event, context_url)
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
                logger.info(
                    f"Candidate {candidate.id} scored {scored.score} -> {scored.decision}"
                )
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
                logger.debug(f"Candidate {candidate.id} skipped: {scored.reason_codes}")
                
        except Exception as exc:
            logger.error(f"Error processing X event: {exc}", exc_info=True)

    async def _process_event_with_semaphore(self, event: dict[str, Any], context_url: str) -> None:
        async with self._process_semaphore:
            await self.process_event(event, context_url)

    def _schedule_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> None:
        if len(self._notification_tasks) >= self.max_pending_notifications:
            logger.warning(
                "Skipping X review notification for %s: pending queue saturated (%d)",
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
