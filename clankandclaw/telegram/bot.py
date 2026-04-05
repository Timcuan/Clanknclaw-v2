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
    # Dummy types for when aiogram is not available
    Bot = Any  # type: ignore
    Dispatcher = Any  # type: ignore
    CallbackQuery = Any  # type: ignore
    InlineKeyboardMarkup = Any  # type: ignore
    InlineKeyboardButton = Any  # type: ignore
    Message = Any  # type: ignore

logger = logging.getLogger(__name__)


def build_review_message(candidate_id: str, review_priority: str, score: int, reason_codes: list[str]) -> str:
    """Build a review message for Telegram."""
    reasons = ", ".join(reason_codes)
    priority_emoji = "🔥" if review_priority == "priority_review" else "📋"
    
    return (
        f"{priority_emoji} <b>New Token Candidate</b>\n\n"
        f"<b>ID:</b> <code>{candidate_id}</code>\n"
        f"<b>Priority:</b> {review_priority}\n"
        f"<b>Score:</b> {score}\n"
        f"<b>Reasons:</b> {reasons}"
    )


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

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        if not AIOGRAM_AVAILABLE:
            raise ImportError("aiogram is required for TelegramBot")
        
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required")
        
        self.bot: Bot = Bot(token=self.token)
        self.dp: Dispatcher = Dispatcher()
        self._setup_handlers()
        
        # Callback handlers (to be set by worker)
        self.on_approve: Any = None
        self.on_reject: Any = None

    def _setup_handlers(self) -> None:
        """Setup message and callback handlers."""
        self.dp.message.register(self._handle_start, Command("start"))
        self.dp.message.register(self._handle_help, Command("help"))
        self.dp.message.register(self._handle_status, Command("status"))
        self.dp.callback_query.register(self._handle_approve, F.data.startswith("approve:"))
        self.dp.callback_query.register(self._handle_reject, F.data.startswith("reject:"))

    async def _handle_start(self, message: Message) -> None:
        """Handle /start command."""
        await message.answer(
            "🤖 <b>Clank&Claw Bot</b>\n\n"
            "I'll send you token candidates for review.\n"
            "Use the buttons to approve or reject each candidate.\n\n"
            "Commands:\n"
            "/help - Show this help\n"
            "/status - Show bot status",
            parse_mode="HTML",
        )

    async def _handle_help(self, message: Message) -> None:
        """Handle /help command."""
        await message.answer(
            "📚 <b>Help</b>\n\n"
            "<b>Review Flow:</b>\n"
            "1. I send you a candidate\n"
            "2. You click Approve or Reject\n"
            "3. I process your decision\n\n"
            "<b>Commands:</b>\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/status - Show bot status",
            parse_mode="HTML",
        )

    async def _handle_status(self, message: Message) -> None:
        """Handle /status command."""
        await message.answer(
            "✅ <b>Bot Status</b>\n\n"
            "Status: Running\n"
            "Ready to receive candidates",
            parse_mode="HTML",
        )

    async def _handle_approve(self, callback: CallbackQuery) -> None:
        """Handle approve button click."""
        if not callback.data:
            return
        
        candidate_id = callback.data.split(":", 1)[1]
        logger.info(f"Approve callback for candidate {candidate_id}")
        
        # Call the approval handler if set
        if self.on_approve:
            try:
                await self.on_approve(candidate_id)
                await callback.message.edit_text(
                    f"✅ <b>Approved</b>\n\n"
                    f"Candidate <code>{candidate_id}</code> has been approved.\n"
                    f"Deploy preparation in progress...",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(f"Error in approve handler: {exc}", exc_info=True)
                await callback.answer(f"Error: {exc}", show_alert=True)
        else:
            await callback.answer("Approval handler not configured", show_alert=True)
        
        await callback.answer()

    async def _handle_reject(self, callback: CallbackQuery) -> None:
        """Handle reject button click."""
        if not callback.data:
            return
        
        candidate_id = callback.data.split(":", 1)[1]
        logger.info(f"Reject callback for candidate {candidate_id}")
        
        # Call the rejection handler if set
        if self.on_reject:
            try:
                await self.on_reject(candidate_id)
                await callback.message.edit_text(
                    f"❌ <b>Rejected</b>\n\n"
                    f"Candidate <code>{candidate_id}</code> has been rejected.",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.error(f"Error in reject handler: {exc}", exc_info=True)
                await callback.answer(f"Error: {exc}", show_alert=True)
        else:
            await callback.answer("Rejection handler not configured", show_alert=True)
        
        await callback.answer()

    async def send_review_notification(
        self,
        candidate_id: str,
        review_priority: str,
        score: int,
        reason_codes: list[str],
    ) -> int | None:
        """Send a review notification to Telegram."""
        try:
            message_text = build_review_message(candidate_id, review_priority, score, reason_codes)
            keyboard = build_review_keyboard(candidate_id)
            
            result = await self.bot.send_message(
                chat_id=self.chat_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            
            logger.info(f"Sent review notification for {candidate_id}, message_id={result.message_id}")
            return result.message_id
            
        except Exception as exc:
            logger.error(f"Error sending review notification: {exc}", exc_info=True)
            return None

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
