"""X detector worker for polling and processing X signals."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
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
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.keywords = keywords or ["deploy", "launch"]
        self.max_results = max_results
        self.target_handles = [h.lower().lstrip("@") for h in (target_handles or ["bankrbot", "clankerdeploy"])]
        self.query_terms = query_terms or ["deploy", "launch", "contract", "ca", "token"]
        self.max_process_concurrency = max(1, max_process_concurrency)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None  # Will be set by supervisor
        self._api: Any = None  # twscrape API instance
        self._last_poll_time: datetime | None = None
        self._seen_tweet_ids: deque[str] = deque(maxlen=5000)
        self._process_semaphore = asyncio.Semaphore(self.max_process_concurrency)

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
        logger.info("X detector worker stopped")

    async def _run(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                await self._poll_and_process()
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
            for query in queries:
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
                        processed_count += len(process_tasks)
                        
                except Exception as exc:
                    logger.error("Error searching X query '%s': %s", query, exc, exc_info=True)
                    
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

    async def process_event(self, event: dict[str, Any], context_url: str) -> None:
        """Process a single X event through the pipeline."""
        try:
            candidate = normalize_x_event(event, context_url)
            scored = await asyncio.to_thread(process_candidate, self.db, candidate)
            
            if scored.decision in ("review", "priority_review"):
                logger.info(
                    f"Candidate {candidate.id} scored {scored.score} -> {scored.decision}"
                )
                
                # Send to Telegram for review
                if self._telegram_worker:
                    await self._telegram_worker.send_review_notification(
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
