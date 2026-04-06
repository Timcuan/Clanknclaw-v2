"""Telegram worker for handling approval flow."""

import asyncio
import json
import logging
import uuid
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
        message_thread_id: int | None = None,
        thread_review_id: int | None = None,
        thread_deploy_id: int | None = None,
        thread_claim_id: int | None = None,
        thread_ops_id: int | None = None,
        thread_alert_id: int | None = None,
    ):
        self.db = db
        self.review_expiry_seconds = review_expiry_seconds
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._message_thread_id = message_thread_id
        self._thread_review_id = thread_review_id
        self._thread_deploy_id = thread_deploy_id
        self._thread_claim_id = thread_claim_id
        self._thread_ops_id = thread_ops_id
        self._thread_alert_id = thread_alert_id
        self.review_queue = ReviewQueue(db)
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._bot: TelegramBot | None = None
        self._deploy_preparation: Any = None  # Will be set by supervisor
        self._rewards_claimer: Any = None
        self._ops_cache_expires_at: float = 0.0
        self._ops_cache_ttl_seconds: float = 2.0
        self._ops_cache: dict[str, str] = {
            "ops.mode": "review",
            "ops.bot_enabled": "on",
            "ops.deployer_mode": "clanker",
        }

    def _refresh_ops_cache_if_needed(self) -> None:
        now = perf_counter()
        if now < self._ops_cache_expires_at:
            return
        keys = ("ops.mode", "ops.bot_enabled", "ops.deployer_mode")
        for key in keys:
            value = self._runtime_get(key)
            if value is not None:
                self._ops_cache[key] = str(value).strip().lower()
        self._ops_cache_expires_at = now + self._ops_cache_ttl_seconds

    def _runtime_get(self, key: str) -> str | None:
        if not hasattr(self.db, "get_runtime_setting"):
            return None
        try:
            return self.db.get_runtime_setting(key)
        except Exception as exc:
            logger.error("Failed reading runtime setting %s: %s", key, exc, exc_info=True)
            return None

    def _ops_mode(self) -> str:
        self._refresh_ops_cache_if_needed()
        mode = self._ops_cache.get("ops.mode", "review")
        return mode if mode in {"review", "auto"} else "review"

    def _bot_enabled(self) -> bool:
        self._refresh_ops_cache_if_needed()
        value = self._ops_cache.get("ops.bot_enabled", "on")
        return value in {"on", "true", "1", "yes"}

    def _deployer_mode(self) -> str:
        self._refresh_ops_cache_if_needed()
        mode = self._ops_cache.get("ops.deployer_mode", "clanker")
        return mode if mode in {"clanker", "bankr", "both"} else "clanker"

    def set_deploy_preparation(self, deploy_preparation: Any) -> None:
        """Set the deploy preparation handler."""
        self._deploy_preparation = deploy_preparation

    def set_rewards_claimer(self, rewards_claimer: Any) -> None:
        """Set the optional rewards claimer."""
        self._rewards_claimer = rewards_claimer

    async def start(self) -> None:
        """Start the Telegram worker."""
        if self._running:
            logger.warning("Telegram worker already running")
            return

        try:
            self._bot = TelegramBot(
                token=self._bot_token or None,
                chat_id=self._chat_id or None,
                message_thread_id=self._message_thread_id,
                thread_review_id=self._thread_review_id,
                thread_deploy_id=self._thread_deploy_id,
                thread_claim_id=self._thread_claim_id,
                thread_ops_id=self._thread_ops_id,
                thread_alert_id=self._thread_alert_id,
                db=self.db,
            )

            # Set callback handlers
            self._bot.on_approve = self._handle_approve
            self._bot.on_reject = self._handle_reject
            self._bot.on_claim_fees = self._handle_claim_fees

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
            if self._ops_mode() == "auto" and review_priority == "priority_review":
                logger.info("Auto mode active; auto-approving %s", candidate_id)
                review_id = f"review-{candidate_id}"
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=self.review_expiry_seconds)
                ).isoformat()
                self.review_queue.create(review_id, candidate_id, expires_at)
                try:
                    await self._handle_approve(candidate_id)
                except Exception as exc:
                    logger.error("Auto-approve deploy failed for %s: %s", candidate_id, exc, exc_info=True)
                return review_id

            if not self._bot_enabled():
                logger.info("ops.bot_enabled=off; skipping review notification for %s", candidate_id)
                return None

            row = self.db.get_candidate(candidate_id)
            raw_text: str | None = None
            source: str | None = None
            context_url: str | None = None
            author_handle: str | None = None
            metadata: dict[str, Any] = {}

            if row:
                raw_text = row["raw_text"]
                source = row["source"]
                try:
                    meta = json.loads(row["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                metadata = meta
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
                metadata=metadata,
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
                deployer_mode = self._deployer_mode()
                if deployer_mode != "clanker":
                    raise RuntimeError(
                        f"deployer_mode={deployer_mode} is not implemented yet; set deployer_mode=clanker"
                    )
                # Notify that preparation has started
                if self._bot and self._bot_enabled():
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
        if not self._bot or not self._bot_enabled():
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

    async def _handle_claim_fees(self, token_address: str) -> Any:
        """Handle manual claim fees request from Telegram command."""
        if not self._rewards_claimer:
            raise ValueError("Rewards claimer is not configured")
        result = await self._rewards_claimer.claim(token_address)
        self.db.save_reward_claim_result(
            result_id=str(uuid.uuid4()),
            token_address=token_address,
            status=result.status,
            tx_hash=result.tx_hash,
            error_code=result.error_code,
            error_message=result.error_message,
            claimed_at=datetime.now(timezone.utc).isoformat(),
        )
        return result
