"""Telegram bot for approval flow."""

import asyncio
import html
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
    AIOGRAM_AVAILABLE = True
except ImportError:
    AIOGRAM_AVAILABLE = False
    Bot = Any  # type: ignore
    Dispatcher = Any  # type: ignore
    CallbackQuery = Any  # type: ignore
    InlineKeyboardMarkup = Any  # type: ignore
    InlineKeyboardButton = Any  # type: ignore
    Message = Any  # type: ignore
    BotCommand = Any  # type: ignore

logger = logging.getLogger(__name__)

_MAX_RAW_TEXT = 300  # chars shown in review message
_MAX_QUEUE_ITEMS = 10
_MAX_ERROR_TEXT = 80
_MAX_REASONS = 6


def _source_label(source: str | None) -> str:
    return {
        "x": "X / Twitter",
        "farcaster": "Farcaster",
        "gecko": "GeckoTerminal",
        "gmgn": "GMGN",
    }.get(source or "", source or "unknown")


def _fmt_text(value: Any, *, fallback: str = "n/a") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return html.escape(text) if text else fallback


def _fmt_inline_code(value: Any, *, fallback: str = "n/a") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return f"<code>{html.escape(text)}</code>" if text else fallback


def _fmt_num(value: Any, *, digits: int = 0, fallback: str = "n/a") -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if digits <= 0:
        return f"{int(num):,}"
    return f"{num:,.{digits}f}"


def _shorten_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


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
    metadata: dict[str, Any] | None = None,
) -> str:
    """Build a review message for Telegram."""
    metadata = metadata or {}
    reason_view = reason_codes[:_MAX_REASONS]
    reasons = ", ".join(reason_view) if reason_view else "—"
    if len(reason_codes) > _MAX_REASONS:
        reasons += f" (+{len(reason_codes) - _MAX_REASONS})"
    priority_emoji = "🔥" if review_priority == "priority_review" else "📋"
    source_label = _source_label(source)
    network = _fmt_text(metadata.get("network"), fallback="unknown")
    confidence_tier = _fmt_text(metadata.get("confidence_tier"), fallback="n/a")
    gate_stage = _fmt_text(metadata.get("gate_stage"), fallback="n/a")
    liquidity_usd = _fmt_num(metadata.get("liquidity_usd"), digits=2, fallback="0.00")
    volume = metadata.get("volume") or {}
    tx_data = metadata.get("transactions") or {}
    volume_m1 = _fmt_num(volume.get("m1"), digits=2, fallback="0.00")
    volume_m5 = _fmt_num(volume.get("m5"), digits=2, fallback="0.00")
    volume_m15 = _fmt_num(volume.get("m15"), digits=2, fallback="0.00")
    tx_m1 = _fmt_num(tx_data.get("m1"), fallback="0")
    tx_m5 = _fmt_num(tx_data.get("m5"), fallback="0")
    contracts = [*list(metadata.get("evm_contracts") or []), *list(metadata.get("sol_contracts") or [])]
    contracts = [str(item) for item in contracts if str(item).strip()]
    contract_hint = ", ".join(_fmt_inline_code(item) for item in contracts[:2]) if contracts else "n/a"
    if len(contracts) > 2:
        contract_hint += f" (+{len(contracts) - 2})"

    lines = [
        f"{priority_emoji} <b>Review Candidate</b>",
        "",
        "<b>Overview</b>",
        f"• <b>ID:</b> {_fmt_inline_code(candidate_id)}",
        f"<b>Source:</b> {source_label}",
        f"• <b>Priority:</b> {_fmt_text(review_priority)}",
        f"• <b>Score:</b> {_fmt_num(score)}",
        "",
        "<b>Momentum</b>",
        f"• <b>Chain:</b> {network}",
        f"• <b>Confidence:</b> {confidence_tier}",
        f"• <b>Gate:</b> {gate_stage}",
        f"• <b>Volume:</b> m1 ${volume_m1} | m5 ${volume_m5} | m15 ${volume_m15}",
        f"• <b>Tx:</b> m1 {tx_m1} | m5 {tx_m5}",
        f"• <b>Liquidity:</b> ${liquidity_usd}",
        "",
        "<b>Risk / Signals</b>",
        f"• <b>Contract hints:</b> {contract_hint}",
        f"• <b>Signals:</b> {_fmt_text(reasons, fallback='—')}",
    ]

    if author_handle:
        lines.append(f"• <b>Author:</b> @{_fmt_text(author_handle, fallback='unknown')}")

    if context_url:
        safe_url = html.escape(context_url, quote=True)
        lines.append(f'• <b>Link:</b> <a href="{safe_url}">Open source</a>')

    if raw_text:
        trimmed = _shorten_text(raw_text, _MAX_RAW_TEXT)
        lines += ["", "<b>Context Excerpt</b>", f"<blockquote>{_fmt_text(trimmed)}</blockquote>"]

    return "\n".join(lines)


def build_queue_message(rows: list[Any]) -> str:
    """Build compact queue message from pending-review rows."""
    if not rows:
        return "📭 No pending reviews."

    lines = [f"📋 <b>Pending Queue</b>", f"Total: <b>{len(rows)}</b>", ""]
    for row in rows[:_MAX_QUEUE_ITEMS]:
        score = row["score"] if row["score"] is not None else "?"
        reasons = row["reason_codes"] or "—"
        lines.append(
            f"• {_fmt_inline_code(row['candidate_id'])} | {_source_label(row['source'])} | score {_fmt_text(score)}\n"
            f"  signals: {_fmt_text(_shorten_text(str(reasons), 120), fallback='—')}"
        )
    if len(rows) > _MAX_QUEUE_ITEMS:
        lines.append(f"\n…and {len(rows) - _MAX_QUEUE_ITEMS} more")
    return "\n".join(lines)


def build_candidate_detail_message(
    candidate: Any,
    decision: Any | None,
    review_item: Any | None,
    deployment: Any | None,
) -> str:
    """Build one-candidate detail message."""
    meta_raw = candidate["metadata_json"] if "metadata_json" in candidate.keys() else "{}"
    try:
        import json
        meta = json.loads(meta_raw or "{}")
    except Exception:
        meta = {}

    lines = [
        "🔎 <b>Candidate Detail</b>",
        "",
        "<b>Overview</b>",
        f"• <b>ID:</b> {_fmt_inline_code(candidate['id'])}",
        f"• <b>Source:</b> {_source_label(candidate['source'])}",
        f"• <b>Author:</b> @{_fmt_text(meta.get('author_handle'))}" if meta.get("author_handle") else "• <b>Author:</b> n/a",
        f"• <b>Link:</b> <a href=\"{html.escape(meta['context_url'], quote=True)}\">Open source</a>" if meta.get("context_url") else "• <b>Link:</b> n/a",
    ]

    if decision:
        lines.extend(
            [
                f"<b>Score:</b> {decision['score']}",
                f"<b>Decision:</b> {decision['decision']}",
                f"<b>Signals:</b> {decision['reason_codes'] or '—'}",
                f"<b>Platform:</b> {decision['recommended_platform']}",
            ]
        )
    else:
        lines.extend(
            [
                "<b>Score:</b> n/a",
                "<b>Decision:</b> n/a",
                "<b>Signals:</b> n/a",
                "<b>Platform:</b> n/a",
            ]
        )

    lines.append(f"<b>Review:</b> {review_item['status']}" if review_item else "<b>Review:</b> n/a")

    if deployment:
        lines.append(f"<b>Deploy:</b> {deployment['status']}")
        if deployment.get("contract_address"):
            lines.append(f"<b>Contract:</b> <code>{deployment['contract_address']}</code>")
        if deployment.get("tx_hash"):
            lines.append(f"<b>TX:</b> <code>{deployment['tx_hash']}</code>")
    else:
        lines.append("<b>Deploy:</b> n/a")

    raw_text = candidate["raw_text"] or ""
    if raw_text:
        trimmed = raw_text[:_MAX_RAW_TEXT]
        if len(raw_text) > _MAX_RAW_TEXT:
            trimmed += "…"
        lines += ["", f"<blockquote>{trimmed}</blockquote>"]

    return "\n".join(lines)


def build_deploys_message(rows: list[Any]) -> str:
    """Build compact recent deployments message."""
    if not rows:
        return "📭 No deployments yet."

    lines = [f"🚀 <b>Recent Deployments</b>", f"Total: <b>{len(rows)}</b>", ""]
    for row in rows:
        if row["status"] == "deploy_success":
            contract = row["contract_address"] or "n/a"
            tx = row["tx_hash"] or "n/a"
            lines.append(
                f"✅ {_fmt_inline_code(row['candidate_id'])} | "
                f"{_fmt_inline_code(contract)} | {_fmt_inline_code(tx)}"
            )
            continue

        error_code = row["error_code"] or "deploy_failed"
        error_message = (row["error_message"] or "").strip()
        if len(error_message) > _MAX_ERROR_TEXT:
            error_message = error_message[:_MAX_ERROR_TEXT] + "…"
        lines.append(
            f"❌ <code>{row['candidate_id']}</code> | {error_code}"
            + (f" | {_fmt_text(error_message)}" if error_message else "")
        )

    return "\n".join(lines)


def build_review_keyboard(candidate_id: str) -> Any:
    """Build inline keyboard for operator actions."""
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{candidate_id}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{candidate_id}"),
            ],
            [
                InlineKeyboardButton(text="🔎 Detail", callback_data=f"detail:{candidate_id}"),
                InlineKeyboardButton(text="🔄 Refresh", callback_data=f"refresh:{candidate_id}"),
            ],
            [
                InlineKeyboardButton(text="📋 Queue", callback_data="queue"),
                InlineKeyboardButton(text="🚀 Deploys", callback_data="deploys"),
            ],
        ]
    )


class TelegramBot:
    """Telegram bot for operator approval flow."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        message_thread_id: int | None = None,
        db: Any = None,
    ):
        if not AIOGRAM_AVAILABLE:
            raise ImportError("aiogram is required for TelegramBot")

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.message_thread_id = message_thread_id
        self._db = db  # optional DatabaseManager for operator commands
        self._last_operator_thread_id: int | None = None

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
        self.on_claim_fees: Any = None

    def _capture_operator_thread(self, thread_id: Any) -> None:
        try:
            if thread_id is None:
                return
            parsed = int(thread_id)
            if parsed > 0:
                self._last_operator_thread_id = parsed
        except (TypeError, ValueError):
            return

    def _resolve_message_thread_id(self, explicit_thread_id: int | None = None) -> int | None:
        if explicit_thread_id is not None:
            return explicit_thread_id
        if self.message_thread_id is not None:
            return self.message_thread_id
        if self._last_operator_thread_id is not None:
            return self._last_operator_thread_id
        return None

    async def _send_bot_message(
        self,
        *,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Any | None = None,
        disable_web_page_preview: bool = False,
        message_thread_id: int | None = None,
    ) -> Any:
        """Send message with bounded retries for transient Telegram API failures."""
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        resolved_thread_id = self._resolve_message_thread_id(message_thread_id)
        if resolved_thread_id is not None:
            payload["message_thread_id"] = resolved_thread_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True

        for attempt in range(3):
            try:
                return await self.bot.send_message(**payload)
            except Exception as exc:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after and attempt < 2:
                    await asyncio.sleep(float(retry_after) + 0.2)
                    continue
                if attempt < 2:
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                raise

    def _setup_handlers(self) -> None:
        """Setup message and callback handlers."""
        self.dp.message.register(self._handle_start, Command("start"))
        self.dp.message.register(self._handle_help, Command("help"))
        self.dp.message.register(self._handle_status, Command("status"))
        self.dp.message.register(self._handle_queue, Command("queue"))
        self.dp.message.register(self._handle_candidate, Command("candidate"))
        self.dp.message.register(self._handle_deploys, Command("deploys"))
        self.dp.message.register(self._handle_claimfees, Command("claimfees"))
        # Backward-compatible aliases
        self.dp.message.register(self._handle_queue, Command("candidates"))
        self.dp.message.register(self._handle_status, Command("stats"))
        self.dp.message.register(self._handle_deploys, Command("deployments"))
        self.dp.message.register(self._handle_cancel, Command("cancel"))
        self.dp.callback_query.register(self._handle_approve, F.data.startswith("approve:"))
        self.dp.callback_query.register(self._handle_reject, F.data.startswith("reject:"))
        self.dp.callback_query.register(self._handle_detail, F.data.startswith("detail:"))
        self.dp.callback_query.register(self._handle_refresh, F.data.startswith("refresh:"))
        self.dp.callback_query.register(self._handle_quick_queue, F.data == "queue")
        self.dp.callback_query.register(self._handle_quick_deploys, F.data == "deploys")

    def _is_authorized_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.chat_id)

    async def _set_bot_commands(self) -> None:
        """Publish compact slash menu to Telegram."""
        await self.bot.set_my_commands(
            [
                BotCommand(command="status", description="Operational status"),
                BotCommand(command="queue", description="Pending review queue"),
                BotCommand(command="candidate", description="Candidate detail by ID"),
                BotCommand(command="deploys", description="Recent deployments"),
                BotCommand(command="claimfees", description="Claim rewards by token"),
                BotCommand(command="help", description="Usage guide"),
            ]
        )

    # ── command handlers ──────────────────────────────────────────────────────

    async def _handle_start(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        await message.answer(
            "🤖 <b>Clank&Claw Bot</b>\n\n"
            "Manual review mode is active.\n"
            "Approve/reject is done per candidate from inline buttons.\n\n"
            "<b>Commands:</b>\n"
            "/status — Operational counters\n"
            "/queue — Pending queue\n"
            "/candidate &lt;id&gt; — Candidate detail\n"
            "/deploys — Recent deployments\n"
            "/claimfees &lt;token_address&gt; — Claim token rewards\n"
            "/help — This help",
            parse_mode="HTML",
        )

    async def _handle_help(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        await message.answer(
            "📚 <b>Help</b>\n\n"
            "<b>Review Flow</b>\n"
            "1. New candidate arrives with ✅/❌ buttons\n"
            "2. Approve → lock + deploy preparation\n"
            "3. Reject → mark rejected immediately\n\n"
            "<b>Commands:</b>\n"
            "/status — Operational counters\n"
            "/queue — Pending queue\n"
            "/candidate &lt;id&gt; — Candidate detail\n"
            "/deploys — Recent deployments\n"
            "/claimfees &lt;token_address&gt; — Claim rewards via Clanker SDK\n"
            "/cancel &lt;id&gt; — Cancel pending review (manual override)\n"
            "/help — Command guide",
            parse_mode="HTML",
        )

    async def _handle_status(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
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

    async def _handle_queue(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_pending_reviews()
            await message.answer(build_queue_message(rows), parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error listing queue: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching queue.", parse_mode="HTML")

    async def _handle_candidate(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /candidate &lt;candidate_id&gt;", parse_mode="HTML")
            return
        candidate_id = parts[1].strip()
        try:
            detail_message = await self._render_candidate_detail(candidate_id)
            await message.answer(
                detail_message,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.error(f"Error fetching candidate detail: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching candidate detail.", parse_mode="HTML")

    async def _handle_deploys(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_recent_deployments(limit=10)
            await message.answer(build_deploys_message(rows), parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error fetching deployments: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching deployments.", parse_mode="HTML")

    async def _handle_cancel(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
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

    async def _handle_claimfees(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        self._capture_operator_thread(getattr(message, "message_thread_id", None))
        if not self.on_claim_fees:
            await message.answer("⚠️ Claim fees handler is not configured.", parse_mode="HTML")
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /claimfees &lt;token_address&gt;", parse_mode="HTML")
            return
        token_address = parts[1].strip()
        try:
            result = await self.on_claim_fees(token_address)
            if result.status == "claim_success":
                tx_line = f"\n• <b>TX:</b> {_fmt_inline_code(result.tx_hash)}" if result.tx_hash else ""
                await message.answer(
                    "💸 <b>Claim Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> success\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}{tx_line}",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "💸 <b>Claim Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> failed\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}\n"
                    f"• <b>Error:</b> {_fmt_text(result.error_code or 'unknown')}\n"
                    f"• <b>Message:</b> {_fmt_text(result.error_message or 'unknown')}",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.error("Error in claim fees handler: %s", exc, exc_info=True)
            await message.answer("⚠️ Claim fees execution failed.", parse_mode="HTML")

    # ── callback handlers ─────────────────────────────────────────────────────

    async def _handle_approve(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))

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
                await callback.answer("Approved")
                return
            except ValueError as exc:
                logger.info("Approve callback stale/already-processed for %s: %s", candidate_id, exc)
                await callback.answer("Already processed or expired", show_alert=True)
                return
            except Exception as exc:
                logger.error(f"Error in approve handler: {exc}", exc_info=True)
                await callback.answer("Approval failed", show_alert=True)
                return
        else:
            await callback.answer("Approval handler not configured", show_alert=True)
            return

        await callback.answer("Approved")

    async def _handle_reject(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))

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
                await callback.answer("Rejected")
                return
            except ValueError as exc:
                logger.info("Reject callback stale/already-processed for %s: %s", candidate_id, exc)
                await callback.answer("Already processed or expired", show_alert=True)
                return
            except Exception as exc:
                logger.error(f"Error in reject handler: {exc}", exc_info=True)
                await callback.answer("Reject failed", show_alert=True)
                return
        else:
            await callback.answer("Rejection handler not configured", show_alert=True)
            return

        await callback.answer("Rejected")

    async def _render_candidate_detail(self, candidate_id: str) -> str:
        if not self._db:
            return "ℹ️ Database not available."
        candidate = self._db.get_candidate(candidate_id)
        if not candidate:
            return f"📭 Candidate {_fmt_inline_code(candidate_id)} not found."
        decision = self._db.get_candidate_decision(candidate_id)
        review_item = self._db.get_review_item(f"review-{candidate_id}")
        deployment = self._db.get_latest_deployment_for_candidate(candidate_id)
        return build_candidate_detail_message(candidate, decision, review_item, deployment)

    async def _handle_detail(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))
        candidate_id = callback.data.split(":", 1)[1]
        try:
            text = await self._render_candidate_detail(candidate_id)
            await callback.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
            await callback.answer("Detail sent")
        except Exception as exc:
            logger.error("Error handling detail callback: %s", exc, exc_info=True)
            await callback.answer("Detail failed", show_alert=True)

    async def _handle_refresh(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))
        candidate_id = callback.data.split(":", 1)[1]
        if not self._db:
            await callback.answer("Database unavailable", show_alert=True)
            return
        try:
            candidate = self._db.get_candidate(candidate_id)
            if not candidate:
                await callback.answer("Candidate not found", show_alert=True)
                return
            decision = self._db.get_candidate_decision(candidate_id)
            if not decision:
                await callback.answer("Decision not ready", show_alert=True)
                return
            try:
                meta = json.loads(candidate["metadata_json"] or "{}")
            except Exception:
                meta = {}
            updated = build_review_message(
                candidate_id,
                "priority_review" if str(decision["decision"]) == "priority_review" else "review",
                int(decision["score"] or 0),
                str(decision["reason_codes"] or "").split(",") if decision["reason_codes"] else [],
                raw_text=candidate["raw_text"],
                source=candidate["source"],
                context_url=meta.get("context_url"),
                author_handle=meta.get("author_handle"),
                metadata=meta,
            )
            await callback.message.edit_text(
                updated,
                parse_mode="HTML",
                reply_markup=build_review_keyboard(candidate_id),
                disable_web_page_preview=True,
            )
            await callback.answer("Refreshed")
        except Exception as exc:
            logger.error("Error refreshing review card: %s", exc, exc_info=True)
            await callback.answer("Refresh failed", show_alert=True)

    async def _handle_quick_queue(self, callback: CallbackQuery) -> None:
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))
        if not self._db:
            await callback.answer("Database unavailable", show_alert=True)
            return
        try:
            rows = self._db.list_pending_reviews()
            await callback.message.answer(build_queue_message(rows), parse_mode="HTML")
            await callback.answer("Queue sent")
        except Exception as exc:
            logger.error("Error handling quick queue callback: %s", exc, exc_info=True)
            await callback.answer("Queue failed", show_alert=True)

    async def _handle_quick_deploys(self, callback: CallbackQuery) -> None:
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        self._capture_operator_thread(getattr(callback.message, "message_thread_id", None))
        if not self._db:
            await callback.answer("Database unavailable", show_alert=True)
            return
        try:
            rows = self._db.list_recent_deployments(limit=10)
            await callback.message.answer(build_deploys_message(rows), parse_mode="HTML")
            await callback.answer("Deploys sent")
        except Exception as exc:
            logger.error("Error handling quick deploys callback: %s", exc, exc_info=True)
            await callback.answer("Deploys failed", show_alert=True)

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
        metadata: dict[str, Any] | None = None,
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
                metadata=metadata,
            )
            keyboard = build_review_keyboard(candidate_id)

            result = await self._send_bot_message(
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
            await self._send_bot_message(
                text=(
                    "⚙️ <b>Deploy Pipeline</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Step:</b> preparing\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}\n"
                    "• <b>Action:</b> fetch image + upload IPFS"
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
            await self._send_bot_message(
                text=(
                    "🎉 <b>Deploy Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> success\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}\n"
                    f"• <b>Contract:</b> {_fmt_inline_code(contract_address)}\n"
                    f"• <b>TX:</b> {_fmt_inline_code(tx_hash)}"
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
            await self._send_bot_message(
                text=(
                    "❌ <b>Deploy Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> failed\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}\n"
                    f"• <b>Error:</b> {_fmt_text(error_code)}\n"
                    f"• <b>Message:</b> {_fmt_text(error_message)}"
                ),
                parse_mode="HTML",
            )
            logger.info(f"Sent deploy failure notification for {candidate_id}")
        except Exception as exc:
            logger.error(f"Error sending deploy failure notification: {exc}", exc_info=True)

    async def start_polling(self) -> None:
        """Start polling for updates."""
        logger.info("Starting Telegram bot polling")
        try:
            await self._set_bot_commands()
        except Exception as exc:
            logger.warning("Failed setting slash commands: %s", exc)
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping Telegram bot")
        await self.bot.session.close()
