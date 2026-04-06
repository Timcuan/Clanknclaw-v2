"""Telegram worker for handling approval flow."""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from clankandclaw.core.review_queue import ReviewQueue
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.telegram.bot import TelegramBot
from clankandclaw.telegram.formatters import _fmt_dashboard_header

logger = logging.getLogger(__name__)
_IPFS_CID_RE = re.compile(r"^[A-Za-z0-9]{32,120}$")


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
        pinata_client: Any = None,
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
        self._pinata = pinata_client
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
        self._manual_deploy_lock = asyncio.Lock()
        self._last_notified: dict[str, float] = {}  # candidate_id -> perf_counter

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
                pinata_client=self._pinata,
            )

            # Set callback handlers
            self._bot.on_approve = self._handle_approve
            self._bot.on_reject = self._handle_reject
            self._bot.on_claim_fees = self._handle_claim_fees
            self._bot.on_manual_deploy = self._handle_manual_deploy
            self._bot.on_manual_deploy_candidate = self._handle_manual_deploy_candidate

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
        auto_trigger: bool = False,
    ) -> str | None:
        """Send a review notification and create review item."""
        if not self._running or not self._bot:
            logger.warning("Telegram worker not running, cannot send notification")
            return None

        try:
            started = perf_counter()
            
            # Check for autonomous deployment trigger
            ops_mode = self._ops_mode()
            auto_threshold = int(self._runtime_get("ops.auto_threshold") or 90)
            
            if ops_mode == "auto" and (auto_trigger or score >= auto_threshold):
                logger.info("Auto mode active; auto-approving %s (score=%d)", candidate_id, score)
                
                # Autonomous Notification (Keep operator informed)
                if self._bot and self._bot_enabled():
                     await self._bot.send_message(
                          _fmt_dashboard_header("Autonomous Deploy", "🤖") +
                          f"High confidence signal detected (Score: <b>{score}</b>).\n"
                          f"Triggering automated deployment sequence...",
                          parse_mode="HTML"
                     )
                
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

            # Secondary throttle: 10 minute cooldown per candidate ID for notifications
            now = perf_counter()
            last_time = self._last_notified.get(candidate_id, 0.0)
            if now - last_time < 600:  # 10 minutes
                logger.debug("Throttling redundant notification for %s", candidate_id)
                return f"review-{candidate_id}"  # Return existing ID format
            self._last_notified[candidate_id] = now

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
                    logger.warning(
                        "deployer_mode=%s not implemented yet; falling back to clanker for %s",
                        deployer_mode,
                        candidate_id,
                    )
                # Notify that preparation has started
                if self._bot and self._bot_enabled():
                    await self._bot.send_deploy_preparing(candidate_id)

                logger.info(f"Starting deploy preparation for {candidate_id}")
                deploy_success = await self._deploy_preparation.prepare_and_deploy(candidate_id)
                self.db.complete_review_item(review_id, success=bool(deploy_success), locked_by="telegram")
            except Exception as exc:
                logger.error(f"Deploy preparation failed: {exc}", exc_info=True)
                self.db.complete_review_item(review_id, success=False, locked_by="telegram")
                if self._bot:
                    await self._bot.send_deploy_failure(
                        candidate_id,
                        "preparation_failed",
                        str(exc),
                    )
        else:
            logger.warning("Deploy preparation handler not set")
            self.db.complete_review_item(review_id, success=False, locked_by="telegram")
            if self._bot:
                await self._bot.send_deploy_failure(
                    candidate_id,
                    "preparation_unavailable",
                    "Deploy preparation handler is not configured",
                )

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

    async def _handle_manual_deploy(
        self,
        platform: str,
        token_name: str,
        token_symbol: str,
        image_ref: str,
        description: str | None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._deploy_preparation:
            raise ValueError("Deploy preparation handler is not configured")
        normalized_platform = platform.strip().lower()
        if normalized_platform != "clanker":
            raise ValueError("Only clanker manual deploy is available right now")

        image_ref_value = (image_ref or "").strip()
        image_metadata: dict[str, Any] = {}
        if image_ref_value and image_ref_value.lower() not in {"auto", "none"}:
            if image_ref_value.startswith(("http://", "https://")):
                image_metadata["image_url"] = image_ref_value
            elif image_ref_value.startswith("ipfs://"):
                cid = image_ref_value[7:].strip()
                if not _IPFS_CID_RE.fullmatch(cid):
                    raise ValueError("Invalid ipfs CID format for image")
                image_metadata["image_uri"] = f"ipfs://{cid}"
            elif _IPFS_CID_RE.fullmatch(image_ref_value):
                image_metadata["image_uri"] = f"ipfs://{image_ref_value}"
            else:
                raise ValueError("image_or_cid must be http(s) URL, ipfs://CID, CID, or auto")

        suffix = uuid.uuid4().hex[:8]
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        candidate_id = f"manual-{suffix}"
        base_text = f"deploy token {token_name} symbol {token_symbol}"
        description_part = (description or "").strip()
        raw_text = f"{base_text}. {description_part}" if description_part else base_text
        metadata = {
            "suggested_name": token_name,
            "suggested_symbol": token_symbol,
            "manual_deploy": True,
            "platform_mode": normalized_platform,
            **image_metadata,
        }
        if context:
            user_id = context.get("user_id")
            username = (context.get("username") or "").strip()
            if user_id:
                metadata["operator_user_id"] = str(user_id)
            if username:
                metadata["author_handle"] = username
            metadata["operator_chat_id"] = str(context.get("chat_id", ""))
            metadata["operator_thread_id"] = context.get("thread_id")

        self.db.save_candidate_and_decision(
            candidate_id=candidate_id,
            source="x",
            source_event_id=f"manual:{suffix}",
            fingerprint=f"manual:{suffix}",
            raw_text=raw_text,
            score=100,
            decision="priority_review",
            reason_codes=["manual_deploy"],
            recommended_platform="clanker",
            observed_at=observed_at,
            metadata=metadata,
        )

        async with self._manual_deploy_lock:
            if self._bot and self._bot_enabled():
                await self._bot.send_deploy_preparing(candidate_id)
            success = await self._deploy_preparation.prepare_and_deploy(candidate_id)

        return {
            "candidate_id": candidate_id,
            "success": bool(success),
        }

    async def _handle_manual_deploy_candidate(
        self,
        platform: str,
        candidate_id: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del context
        if not self._deploy_preparation:
            raise ValueError("Deploy preparation handler is not configured")

        normalized_platform = platform.strip().lower()
        if normalized_platform != "clanker":
            raise ValueError("Only clanker manual deploy is available right now")
        if not self.db.get_candidate(candidate_id):
            raise ValueError(f"Candidate {candidate_id} not found")

        async with self._manual_deploy_lock:
            if self._bot and self._bot_enabled():
                await self._bot.send_deploy_preparing(candidate_id)
            success = await self._deploy_preparation.prepare_and_deploy(candidate_id)

        return {
            "candidate_id": candidate_id,
            "success": bool(success),
        }
