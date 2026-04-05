"""Telegram bot for approval flow."""

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
    AIOGRAM_AVAILABLE = True
except ImportError:
    AIOGRAM_AVAILABLE = False
    Bot = Any  # type: ignore
    Dispatcher = Any  # type: ignore
    CallbackQuery = Any  # type: ignore
    InlineKeyboardMarkup = Any  # type: ignore
    InlineKeyboardButton = Any  # type: ignore
    Message = Any  # type: ignore

logger = logging.getLogger(__name__)

_MAX_RAW_TEXT = 300  # chars shown in review message


def build_review_message(
    candidate_id: str,
    review_priority: str,
    score: int,
    reason_codes: list[str],
    *,
    raw_text: str | None = None,
    source: str | None = None,
    context_url: str | None = None,
    author_handle: str | None = None,
) -> str:
    """Build a review message for Telegram."""
    reasons = ", ".join(reason_codes) if reason_codes else "—"
    priority_emoji = "🔥" if review_priority == "priority_review" else "📋"
    source_label = {"x": "X / Twitter", "gmgn": "GMGN"}.get(source or "", source or "unknown")

    lines = [
        f"{priority_emoji} <b>New Token Candidate</b>",
        "",
        f"<b>ID:</b> <code>{candidate_id}</code>",
        f"<b>Source:</b> {source_label}",
        f"<b>Priority:</b> {review_priority}",
        f"<b>Score:</b> {score}",
        f"<b>Signals:</b> {reasons}",
    ]

    if author_handle:
        lines.append(f"<b>Author:</b> @{author_handle}")

    if context_url:
        lines.append(f'<b>Link:</b> <a href="{context_url}">View original</a>')

    if raw_text:
        trimmed = raw_text[:_MAX_RAW_TEXT]
        if len(raw_text) > _MAX_RAW_TEXT:
            trimmed += "…"
        lines += ["", f"<blockquote>{trimmed}</blockquote>"]

    return "\n".join(lines)


def build_review_keyboard(candidate_id: str) -> Any:
    """Build inline keyboard for approve/reject."""
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{candidate_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{candidate_id}"),
            ]
        ]
    )


class TelegramBot:
    """Telegram bot for operator approval flow."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        db: Any = None,
    ):
        if not AIOGRAM_AVAILABLE:
            raise ImportError("aiogram is required for TelegramBot")

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self._db = db  # optional DatabaseManager for operator commands

        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")

        self.bot: Bot = Bot(token=self.token)
        self.dp: Dispatcher = Dispatcher()
        self._setup_handlers()

        # Callback handlers (set by worker)
        self.on_approve: Any = None
        self.on_reject: Any = None

    def _setup_handlers(self) -> None:
        """Setup message and callback handlers."""
        self.dp.message.register(self._handle_start, Command("start"))
        self.dp.message.register(self._handle_help, Command("help"))
        self.dp.message.register(self._handle_status, Command("status"))
        self.dp.message.register(self._handle_candidates, Command("candidates"))
        self.dp.message.register(self._handle_stats, Command("stats"))
        self.dp.message.register(self._handle_deployments, Command("deployments"))
        self.dp.message.register(self._handle_cancel, Command("cancel"))
        self.dp.callback_query.register(self._handle_approve, F.data.startswith("approve:"))
        self.dp.callback_query.register(self._handle_reject, F.data.startswith("reject:"))

    # ── command handlers ──────────────────────────────────────────────────────

    async def _handle_start(self, message: Message) -> None:
        await message.answer(
            "🤖 <b>Clank&Claw Bot</b>\n\n"
            "I'll send you token candidates for review.\n"
            "Use the buttons to approve or reject each candidate.\n\n"
            "<b>Commands:</b>\n"
            "/status — Bot and worker status\n"
            "/candidates — List pending reviews\n"
            "/stats — Deployment statistics\n"
            "/deployments — Recent deployments\n"
            "/cancel &lt;id&gt; — Cancel a pending review\n"
            "/help — This help",
            parse_mode="HTML",
        )

    async def _handle_help(self, message: Message) -> None:
        await message.answer(
            "📚 <b>Help</b>\n\n"
            "<b>Review Flow:</b>\n"
            "1. I send you a candidate with ✅ Approve / ❌ Reject buttons\n"
            "2. Approve → deploy preparation starts automatically\n"
            "3. Reject → candidate is discarded\n\n"
            "<b>Commands:</b>\n"
            "/start — Welcome\n"
            "/status — Bot and worker status\n"
            "/candidates — List pending reviews\n"
            "/stats — Deployment statistics\n"
            "/deployments — Recent deployments\n"
            "/cancel &lt;id&gt; — Cancel a pending review\n"
            "/help — This help",
            parse_mode="HTML",
        )

    async def _handle_status(self, message: Message) -> None:
        if self._db:
            try:
                stats = self._db.get_stats()
                await message.answer(
                    "✅ <b>Bot Status</b>\n\n"
                    f"Pending reviews: <b>{stats['pending_reviews']}</b>\n"
                    f"Total candidates seen: <b>{stats['total_candidates']}</b>\n"
                    f"Deployed: <b>{stats['deployed']}</b>\n"
                    f"Deploy failures: <b>{stats['deploy_failed']}</b>\n"
                    f"Rejected: <b>{stats['rejected']}</b>",
                    parse_mode="HTML",
                )
                return
            except Exception as exc:
                logger.error(f"Error fetching status: {exc}", exc_info=True)

        await message.answer("✅ <b>Bot Status</b>\n\nStatus: Running", parse_mode="HTML")

    async def _handle_candidates(self, message: Message) -> None:
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_pending_reviews()
            if not rows:
                await message.answer("📭 No pending reviews.", parse_mode="HTML")
                return
            lines = [f"📋 <b>Pending Reviews ({len(rows)})</b>", ""]
            for row in rows[:10]:
                score = row["score"] if row["score"] is not None else "?"
                lines.append(f"• <code>{row['candidate_id']}</code> — score {score} ({row['source']})")
            if len(rows) > 10:
                lines.append(f"\n…and {len(rows) - 10} more")
            await message.answer("\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error listing candidates: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching candidates.", parse_mode="HTML")

    async def _handle_stats(self, message: Message) -> None:
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            stats = self._db.get_stats()
            await message.answer(
                "📊 <b>Statistics</b>\n\n"
                f"Total candidates: <b>{stats['total_candidates']}</b>\n"
                f"Pending reviews: <b>{stats['pending_reviews']}</b>\n"
                f"Rejected: <b>{stats['rejected']}</b>\n"
                f"Deployed: <b>{stats['deployed']}</b>\n"
                f"Deploy failures: <b>{stats['deploy_failed']}</b>",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(f"Error fetching stats: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching stats.", parse_mode="HTML")

    async def _handle_deployments(self, message: Message) -> None:
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_recent_deployments(limit=10)
            if not rows:
                await message.answer("📭 No deployments yet.", parse_mode="HTML")
                return
            lines = ["🚀 <b>Recent Deployments</b>", ""]
            for row in rows:
                if row["status"] == "deploy_success":
                    lines.append(
                        f"✅ <code>{row['candidate_id']}</code>\n"
                        f"   Contract: <code>{row['contract_address']}</code>\n"
                        f"   TX: <code>{row['tx_hash']}</code>"
                    )
                else:
                    lines.append(
                        f"❌ <code>{row['candidate_id']}</code> — {row['error_code']}: {row['error_message']}"
                    )
            await message.answer("\n\n".join(lines), parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error fetching deployments: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching deployments.", parse_mode="HTML")

    async def _handle_cancel(self, message: Message) -> None:
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /cancel &lt;candidate_id&gt;", parse_mode="HTML")
            return
        candidate_id = parts[1].strip()
        review_id = f"review-{candidate_id}"
        try:
            ok = self._db.reject_review_item(review_id, "operator_cancel")
            if ok:
                await message.answer(
                    f"🚫 Review <code>{candidate_id}</code> cancelled.",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    f"⚠️ Could not cancel <code>{candidate_id}</code> — not found or already processed.",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.error(f"Error cancelling review: {exc}", exc_info=True)
            await message.answer("⚠️ Error cancelling review.", parse_mode="HTML")

    # ── callback handlers ─────────────────────────────────────────────────────

    async def _handle_approve(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return

        candidate_id = callback.data.split(":", 1)[1]
        logger.info(f"Approve callback for candidate {candidate_id}")

        if self.on_approve:
            try:
                await self.on_approve(candidate_id)
                if callback.message:
                    await callback.message.edit_text(
                        f"✅ <b>Approved</b>\n\n"
                        f"Candidate <code>{candidate_id}</code> approved.\n"
                        f"Deploy preparation in progress…",
                        parse_mode="HTML",
                    )
            except Exception as exc:
                logger.error(f"Error in approve handler: {exc}", exc_info=True)
                await callback.answer(f"Error: {exc}", show_alert=True)
                return
        else:
            await callback.answer("Approval handler not configured", show_alert=True)
            return

        await callback.answer()

    async def _handle_reject(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return

        candidate_id = callback.data.split(":", 1)[1]
        logger.info(f"Reject callback for candidate {candidate_id}")

        if self.on_reject:
            try:
                await self.on_reject(candidate_id)
                if callback.message:
                    await callback.message.edit_text(
                        f"❌ <b>Rejected</b>\n\n"
                        f"Candidate <code>{candidate_id}</code> has been rejected.",
                        parse_mode="HTML",
                    )
            except Exception as exc:
                logger.error(f"Error in reject handler: {exc}", exc_info=True)
                await callback.answer(f"Error: {exc}", show_alert=True)
                return
        else:
            await callback.answer("Rejection handler not configured", show_alert=True)
            return

        await callback.answer()

    # ── notification helpers ──────────────────────────────────────────────────

    async def send_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
        *,
        raw_text: str | None = None,
        source: str | None = None,
        context_url: str | None = None,
        author_handle: str | None = None,
    ) -> int | None:
        """Send a review notification to Telegram. Returns message_id or None."""
        try:
            message_text = build_review_message(
                candidate_id,
                review_priority,
                score,
                reason_codes,
                raw_text=raw_text,
                source=source,
                context_url=context_url,
                author_handle=author_handle,
            )
            keyboard = build_review_keyboard(candidate_id)

            result = await self.bot.send_message(
                chat_id=self.chat_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

            logger.info(f"Sent review notification for {candidate_id}, message_id={result.message_id}")
            return result.message_id

        except Exception as exc:
            logger.error(f"Error sending review notification: {exc}", exc_info=True)
            return None

    async def send_deploy_preparing(self, candidate_id: str) -> None:
        """Notify that deploy preparation has started."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    f"⚙️ <b>Preparing Deploy</b>\n\n"
                    f"<b>Candidate:</b> <code>{candidate_id}</code>\n"
                    f"Fetching image, uploading to IPFS…"
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.error(f"Error sending prepare notification: {exc}", exc_info=True)

    async def send_deploy_success(
        self,
        candidate_id: str,
        tx_hash: str,
        contract_address: str,
    ) -> None:
        """Send deploy success notification."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    f"🎉 <b>Deploy Success</b>\n\n"
                    f"<b>Candidate:</b> <code>{candidate_id}</code>\n"
                    f"<b>Contract:</b> <code>{contract_address}</code>\n"
                    f"<b>TX:</b> <code>{tx_hash}</code>"
                ),
                parse_mode="HTML",
            )
            logger.info(f"Sent deploy success notification for {candidate_id}")
        except Exception as exc:
            logger.error(f"Error sending deploy success notification: {exc}", exc_info=True)

    async def send_deploy_failure(
        self,
        candidate_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        """Send deploy failure notification."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=(
                    f"❌ <b>Deploy Failed</b>\n\n"
                    f"<b>Candidate:</b> <code>{candidate_id}</code>\n"
                    f"<b>Error:</b> {error_code}\n"
                    f"<b>Message:</b> {error_message}"
                ),
                parse_mode="HTML",
            )
            logger.info(f"Sent deploy failure notification for {candidate_id}")
        except Exception as exc:
            logger.error(f"Error sending deploy failure notification: {exc}", exc_info=True)

    async def start_polling(self) -> None:
        """Start polling for updates."""
        logger.info("Starting Telegram bot polling")
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping Telegram bot")
        await self.bot.session.close()
