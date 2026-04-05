"""GMGN detector worker for polling and processing GMGN signals."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

from clankandclaw.core.detectors.gmgn_detector import normalize_gmgn_payload
from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


class GMGNDetectorWorker:
    """Worker that polls GMGN for new token launches and processes them through the pipeline."""

    def __init__(
        self,
        db: DatabaseManager,
        poll_interval: float = 60.0,
        api_url: str = "https://gmgn.ai/defi/quotation/v1/tokens/base/new",
        max_results: int = 20,
    ):
        self.db = db
        self.poll_interval = poll_interval
        self.api_url = api_url
        self.max_results = max_results
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._telegram_worker: Any = None  # Will be set by supervisor
        self._last_poll_time: datetime | None = None
        self._seen_tokens: deque[str] = deque(maxlen=1000)

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        """Set the telegram worker for sending notifications."""
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        """Start the GMGN detector worker."""
        if self._running:
            logger.warning("GMGN detector worker already running")
            return

        self._running = True
        self._last_poll_time = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run())
        logger.info("GMGN detector worker started")

    async def stop(self) -> None:
        """Stop the GMGN detector worker."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("GMGN detector worker stopped")

    async def _run(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                await self._poll_and_process()
            except Exception as exc:
                logger.error(f"Error in GMGN detector worker: {exc}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def _poll_and_process(self) -> None:
        """Poll GMGN for new token launches and process them."""
        try:
            logger.debug(f"Polling GMGN API: {self.api_url}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.api_url,
                    params={"limit": self.max_results},
                )
                response.raise_for_status()
                data = response.json()
            
            # Extract tokens from response
            tokens = data.get("data", {}).get("tokens", [])
            logger.info(f"Found {len(tokens)} new tokens from GMGN")
            
            # Process each token
            for token in tokens:
                token_address = token.get("address")
                
                # Skip if we've already seen this token
                if token_address in self._seen_tokens:
                    continue

                self._seen_tokens.append(token_address)
                
                # Build payload for normalization
                payload = {
                    "id": token_address,
                    "text": self._build_token_description(token),
                    "author": "gmgn",
                    "token_data": token,
                }
                
                context_url = f"https://gmgn.ai/base/token/{token_address}"
                
                await self.process_payload(payload, context_url)
            
            self._last_poll_time = datetime.now(timezone.utc)
                
        except httpx.HTTPError as exc:
            logger.error(f"HTTP error polling GMGN: {exc}")
        except Exception as exc:
            logger.error(f"Error in GMGN polling: {exc}", exc_info=True)

    def _build_token_description(self, token: dict[str, Any]) -> str:
        """Build a text description from token data."""
        name = token.get("name", "Unknown")
        symbol = token.get("symbol", "???")
        
        # Try to extract any description or social links
        description_parts = [f"New token launch: {name} ({symbol})"]
        
        if "twitter" in token:
            description_parts.append(f"Twitter: {token['twitter']}")
        
        if "telegram" in token:
            description_parts.append(f"Telegram: {token['telegram']}")
        
        if "website" in token:
            description_parts.append(f"Website: {token['website']}")
        
        return " | ".join(description_parts)

    async def process_payload(self, payload: dict[str, Any], context_url: str) -> None:
        """Process a single GMGN payload through the pipeline."""
        try:
            candidate = normalize_gmgn_payload(payload, context_url)
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
            logger.error(f"Error processing GMGN payload: {exc}", exc_info=True)
