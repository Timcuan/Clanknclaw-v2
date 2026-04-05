"""X detector worker for polling and processing X signals."""

import asyncio
import logging
from datetime import datetime, timezone
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
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.keywords = keywords or ["deploy", "launch"]
        self.max_results = max_results
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None  # Will be set by supervisor
        self._api: Any = None  # twscrape API instance
        self._last_poll_time: datetime | None = None

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
            # Search for tweets with deploy keywords
            for keyword in self.keywords:
                logger.debug(f"Searching X for keyword: {keyword}")
                
                try:
                    # Use twscrape to search for recent tweets
                    tweets = []
                    async for tweet in self._api.search(
                        f"{keyword} token",
                        limit=self.max_results,
                    ):
                        tweets.append(tweet)
                    
                    logger.info(f"Found {len(tweets)} tweets for keyword '{keyword}'")
                    
                    # Process each tweet
                    for tweet in tweets:
                        # Convert tweet to dict format expected by normalize_x_event
                        event = {
                            "id": str(tweet.id),
                            "text": tweet.rawContent,
                            "user": {
                                "username": tweet.user.username if tweet.user else "unknown",
                            },
                            "created_at": tweet.date.isoformat() if tweet.date else None,
                        }
                        
                        context_url = f"https://x.com/{event['user']['username']}/status/{tweet.id}"
                        
                        await self.process_event(event, context_url)
                        
                except Exception as exc:
                    logger.error(f"Error searching for keyword '{keyword}': {exc}", exc_info=True)
                    
            self._last_poll_time = datetime.now(timezone.utc)
            
        except Exception as exc:
            logger.error(f"Error in X polling: {exc}", exc_info=True)

    async def process_event(self, event: dict[str, Any], context_url: str) -> None:
        """Process a single X event through the pipeline."""
        try:
            candidate = normalize_x_event(event, context_url)
            scored = process_candidate(self.db, candidate)
            
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
