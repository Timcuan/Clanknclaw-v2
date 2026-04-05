"""Telegram worker for handling approval flow."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from clankandclaw.core.review_queue import ReviewQueue
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.telegram.bot import TelegramBot

logger = logging.getLogger(__name__)


class TelegramWorker:
    """Worker that handles Telegram bot for approval flow."""

    def __init__(
        self,
        db: DatabaseManager,
        review_expiry_seconds: int = 900,
    ):
        self.db = db
        self.review_expiry_seconds = review_expiry_seconds
        self.review_queue = ReviewQueue(db)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._bot: TelegramBot | None = None
        self._deploy_preparation: Any = None  # Will be set by supervisor

    def set_deploy_preparation(self, deploy_preparation: Any) -> None:
        """Set the deploy preparation handler."""
        self._deploy_preparation = deploy_preparation

    async def start(self) -> None:
        """Start the Telegram worker."""
        if self._running:
            logger.warning("Telegram worker already running")
            return

        try:
            # Initialize bot
            self._bot = TelegramBot()
            
            # Set callback handlers
            self._bot.on_approve = self._handle_approve
            self._bot.on_reject = self._handle_reject
            
            self._running = True
            self._task = asyncio.create_task(self._run())
            logger.info("Telegram worker started")
            
        except ImportError as exc:
            logger.warning(f"Telegram worker disabled: {exc}")
        except ValueError as exc:
            logger.warning(f"Telegram worker disabled: {exc}")
        except Exception as exc:
            logger.error(f"Failed to start Telegram worker: {exc}", exc_info=True)

    async def stop(self) -> None:
        """Stop the Telegram worker."""
        if not self._running:
            return

        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self._bot:
            await self._bot.stop()
        
        logger.info("Telegram worker stopped")

    async def _run(self) -> None:
        """Main worker loop - start bot polling."""
        if not self._bot:
            return
        
        try:
            await self._bot.start_polling()
        except asyncio.CancelledError:
            logger.info("Telegram polling cancelled")
        except Exception as exc:
            logger.error(f"Error in Telegram polling: {exc}", exc_info=True)

    async def send_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> str | None:
        """Send a review notification and create review item."""
        if not self._running or not self._bot:
            logger.warning("Telegram worker not running, cannot send notification")
            return None

        try:
            # Send notification
            message_id = await self._bot.send_review_notification(
                candidate_id, review_priority, score, reason_codes
            )
            
            if not message_id:
                logger.error(f"Failed to send notification for {candidate_id}")
                return None
            
            # Create review item
            review_id = f"review-{candidate_id}"
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.review_expiry_seconds)
            ).isoformat()
            
            self.review_queue.create(review_id, candidate_id, expires_at)
            logger.info(f"Created review item {review_id} for {candidate_id}")
            
            return review_id
            
        except Exception as exc:
            logger.error(f"Error sending review notification: {exc}", exc_info=True)
            return None

    async def _handle_approve(self, candidate_id: str) -> None:
        """Handle approval callback."""
        logger.info(f"Processing approval for {candidate_id}")
        
        review_id = f"review-{candidate_id}"
        
        # Lock the review item
        locked = self.review_queue.lock(review_id, "telegram")
        if not locked:
            logger.warning(f"Failed to lock review item {review_id}")
            raise ValueError("Review item already processed or expired")
        
        logger.info(f"Approved and locked {review_id}")
        
        # Trigger deploy preparation if handler is set
        if self._deploy_preparation:
            try:
                logger.info(f"Starting deploy preparation for {candidate_id}")
                await self._deploy_preparation.prepare_and_deploy(candidate_id)
            except Exception as exc:
                logger.error(f"Deploy preparation failed: {exc}", exc_info=True)
                # Send failure notification
                if self._bot:
                    await self._bot.send_deploy_failure(
                        candidate_id,
                        "preparation_failed",
                        str(exc),
                    )
        else:
            logger.warning("Deploy preparation handler not set")

    async def _handle_reject(self, candidate_id: str) -> None:
        """Handle rejection callback."""
        logger.info(f"Processing rejection for {candidate_id}")
        
        review_id = f"review-{candidate_id}"
        
        # Lock the review item (to prevent duplicate processing)
        locked = self.review_queue.lock(review_id, "telegram")
        if not locked:
            logger.warning(f"Failed to lock review item {review_id}")
            raise ValueError("Review item already processed or expired")
        
        logger.info(f"Rejected and locked {review_id}")

    async def send_deploy_success(
        self,
        candidate_id: str,
        tx_hash: str,
        contract_address: str,
    ) -> None:
        """Send deploy success notification."""
        if not self._bot:
            return
        
        await self._bot.send_deploy_success(candidate_id, tx_hash, contract_address)

    async def send_deploy_failure(
        self,
        candidate_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """Send deploy failure notification."""
        if not self._bot:
            return
        
        await self._bot.send_deploy_failure(candidate_id, error_code, error_message)
