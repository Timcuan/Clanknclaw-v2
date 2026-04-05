"""Telegram worker for handling approval flow."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
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
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.db = db
        self.review_expiry_seconds = review_expiry_seconds
        self._bot_token = bot_token
        self._chat_id = chat_id
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
            self._bot = TelegramBot(
                token=self._bot_token or None,
                chat_id=self._chat_id or None,
                db=self.db,
            )

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
            started = perf_counter()
            row = self.db.get_candidate(candidate_id)
            raw_text: str | None = None
            source: str | None = None
            context_url: str | None = None
            author_handle: str | None = None

            if row:
                raw_text = row["raw_text"]
                source = row["source"]
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                context_url = meta.get("context_url")
                author_handle = meta.get("author_handle")

            message_id = await self._bot.send_review_notification(
                candidate_id,
                review_priority,
                score,
                reason_codes,
                raw_text=raw_text,
                source=source,
                context_url=context_url,
                author_handle=author_handle,
            )

            if not message_id:
                logger.error(f"Failed to send notification for {candidate_id}")
                return None

            # Create review item and store message_id
            review_id = f"review-{candidate_id}"
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.review_expiry_seconds)
            ).isoformat()

            self.review_queue.create(review_id, candidate_id, expires_at)
            self.db.set_review_telegram_message_id(review_id, message_id)
            logger.info(
                "telegram.review_notify_ms=%d candidate=%s review_id=%s",
                int((perf_counter() - started) * 1000),
                candidate_id,
                review_id,
            )

            return review_id

        except Exception as exc:
            logger.error(f"Error sending review notification: {exc}", exc_info=True)
            return None

    async def _handle_approve(self, candidate_id: str) -> None:
        """Handle approval callback."""
        logger.info(f"Processing approval for {candidate_id}")

        review_id = f"review-{candidate_id}"

        locked = self.review_queue.lock(review_id, "telegram")
        if not locked:
            logger.warning(f"Failed to lock review item {review_id}")
            raise ValueError("Review item already processed or expired")

        logger.info(f"Approved and locked {review_id}")

        if self._deploy_preparation:
            try:
                # Notify that preparation has started
                if self._bot:
                    await self._bot.send_deploy_preparing(candidate_id)

                logger.info(f"Starting deploy preparation for {candidate_id}")
                await self._deploy_preparation.prepare_and_deploy(candidate_id)
            except Exception as exc:
                logger.error(f"Deploy preparation failed: {exc}", exc_info=True)
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

        rejected = self.db.reject_review_item(review_id, "telegram")
        if not rejected:
            logger.warning(f"Failed to reject review item {review_id}")
            raise ValueError("Review item already processed or expired")

        logger.info(f"Rejected {review_id}")

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
