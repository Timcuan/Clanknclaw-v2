"""Telegram bot for approval flow."""

import asyncio
import secrets
import html
import json
import logging
import os
import re
import shlex
from typing import TYPE_CHECKING, Any, Callable

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
_MAX_CALLBACK_DATA = 64
_THREAD_CATEGORIES = ("review", "deploy", "claim", "ops", "alert")
_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


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


def _is_evm_address(value: str) -> bool:
    return bool(_EVM_ADDRESS_RE.fullmatch(value.strip()))


def _is_private_key(value: str) -> bool:
    return bool(_PRIVATE_KEY_RE.fullmatch(value.strip()))


def _mask_sensitive_wallet(value: str) -> str:
    text = value.strip()
    if _is_private_key(text):
        return f"{text[:8]}…{text[-4:]}"
    if len(text) > 12:
        return f"{text[:8]}…{text[-4:]}"
    return text


def _parse_command_args(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    if not parts:
        return []
    return parts[1:]


def resolve_authorized_chat_id(
    configured_chat_id: str | None,
    runtime_chat_id: str | None,
) -> str | None:
    """Prefer runtime-paired chat id when available; fallback to configured env chat id."""
    if runtime_chat_id is not None and str(runtime_chat_id).strip():
        return str(runtime_chat_id).strip()
    if configured_chat_id is not None and str(configured_chat_id).strip():
        return str(configured_chat_id).strip()
    return None


def build_action_callback_data(
    action: str,
    candidate_id: str,
    *,
    encode_candidate_id: Callable[[str], str] | None = None,
) -> str:
    """Build Telegram callback_data with hard limit enforcement."""
    encoded = encode_candidate_id(candidate_id) if encode_candidate_id else candidate_id
    callback_data = f"{action}:{encoded}"
    if len(callback_data) > _MAX_CALLBACK_DATA:
        raise ValueError(
            f"callback_data too long ({len(callback_data)} > {_MAX_CALLBACK_DATA}) for action={action}"
        )
    return callback_data


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
        lines += ["", f"<blockquote>{_fmt_text(trimmed)}</blockquote>"]

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


def build_review_keyboard(
    candidate_id: str,
    *,
    encode_candidate_id: Callable[[str], str] | None = None,
) -> Any:
    """Build inline keyboard for operator actions."""
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve",
                    callback_data=build_action_callback_data(
                        "approve",
                        candidate_id,
                        encode_candidate_id=encode_candidate_id,
                    ),
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=build_action_callback_data(
                        "reject",
                        candidate_id,
                        encode_candidate_id=encode_candidate_id,
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Detail",
                    callback_data=build_action_callback_data(
                        "detail",
                        candidate_id,
                        encode_candidate_id=encode_candidate_id,
                    ),
                ),
                InlineKeyboardButton(
                    text="🔄 Refresh",
                    callback_data=build_action_callback_data(
                        "refresh",
                        candidate_id,
                        encode_candidate_id=encode_candidate_id,
                    ),
                ),
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
        thread_review_id: int | None = None,
        thread_deploy_id: int | None = None,
        thread_claim_id: int | None = None,
        thread_ops_id: int | None = None,
        thread_alert_id: int | None = None,
        db: Any = None,
    ):
        if not AIOGRAM_AVAILABLE:
            raise ImportError("aiogram is required for TelegramBot")

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        configured_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.chat_id = configured_chat_id
        self.message_thread_id = message_thread_id
        self.thread_review_id = thread_review_id
        self.thread_deploy_id = thread_deploy_id
        self.thread_claim_id = thread_claim_id
        self.thread_ops_id = thread_ops_id
        self.thread_alert_id = thread_alert_id
        self._db = db  # optional DatabaseManager for operator commands
        self._last_operator_thread_id: int | None = None
        self._dynamic_thread_bindings: dict[str, int] = {}
        self._callback_candidate_map: dict[str, str] = {}

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
        self.on_manual_deploy: Any = None
        self.on_manual_deploy_candidate: Any = None
        self._load_dynamic_thread_bindings()
        runtime_chat_id = self._runtime_get("telegram.chat_id")
        resolved_chat_id = resolve_authorized_chat_id(configured_chat_id, runtime_chat_id)
        if resolved_chat_id:
            self.chat_id = resolved_chat_id

    def _persist_dynamic_thread_binding(self, category: str, thread_id: int) -> None:
        if not self._db:
            return
        setter = getattr(self._db, "set_runtime_setting", None)
        if not callable(setter):
            return
        try:
            setter(f"telegram.thread.{category}", str(thread_id))
        except Exception as exc:
            logger.debug("Failed persisting dynamic thread binding %s=%s: %s", category, thread_id, exc)

    def _load_dynamic_thread_bindings(self) -> None:
        if not self._db:
            return
        getter = getattr(self._db, "get_runtime_setting", None)
        if not callable(getter):
            return
        for category in _THREAD_CATEGORIES:
            try:
                raw_value = getter(f"telegram.thread.{category}")
            except Exception as exc:
                logger.debug("Failed loading dynamic thread binding for %s: %s", category, exc)
                continue
            if raw_value is None:
                continue
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                self._dynamic_thread_bindings[category] = parsed

    def _bind_dynamic_thread(self, category: str, thread_id: Any) -> None:
        if category not in _THREAD_CATEGORIES:
            return
        try:
            parsed = int(thread_id)
        except (TypeError, ValueError):
            return
        if parsed <= 0:
            return

        configured = {
            "review": self.thread_review_id,
            "deploy": self.thread_deploy_id,
            "claim": self.thread_claim_id,
            "ops": self.thread_ops_id,
            "alert": self.thread_alert_id,
        }.get(category)
        if configured is not None:
            return

        previous = self._dynamic_thread_bindings.get(category)
        if previous == parsed:
            return
        self._dynamic_thread_bindings[category] = parsed
        self._persist_dynamic_thread_binding(category, parsed)
        logger.info("telegram.smart_bind category=%s thread_id=%s", category, parsed)

    def _persist_authorized_chat(self, chat_id: Any) -> None:
        if not self._db:
            return
        setter = getattr(self._db, "set_runtime_setting", None)
        if not callable(setter):
            return
        try:
            setter("telegram.chat_id", str(chat_id))
        except Exception as exc:
            logger.debug("Failed persisting telegram.chat_id=%s: %s", chat_id, exc)

    def _capture_operator_thread(self, thread_id: Any) -> None:
        try:
            if thread_id is None:
                return
            parsed = int(thread_id)
            if parsed > 0:
                self._last_operator_thread_id = parsed
        except (TypeError, ValueError):
            return

    def _encode_callback_candidate_id(self, candidate_id: str) -> str:
        """Encode candidate IDs to callback-safe short tokens when needed."""
        if len(f"refresh:{candidate_id}") <= _MAX_CALLBACK_DATA:
            return candidate_id

        token = f"k:{secrets.token_hex(6)}"
        self._callback_candidate_map[token] = candidate_id
        if self._db:
            setter = getattr(self._db, "set_runtime_setting", None)
            if callable(setter):
                try:
                    setter(f"telegram.callback.{token}", candidate_id)
                except Exception as exc:
                    logger.debug("Failed persisting callback token %s: %s", token, exc)
        return token

    def _decode_callback_candidate_id(self, encoded_id: str) -> str:
        """Resolve callback-safe token back to original candidate ID."""
        if not encoded_id.startswith("k:"):
            return encoded_id

        mapped = self._callback_candidate_map.get(encoded_id)
        if mapped:
            return mapped

        if self._db:
            getter = getattr(self._db, "get_runtime_setting", None)
            if callable(getter):
                try:
                    persisted = getter(f"telegram.callback.{encoded_id}")
                except Exception as exc:
                    logger.debug("Failed loading callback token %s: %s", encoded_id, exc)
                    persisted = None
                if persisted:
                    self._callback_candidate_map[encoded_id] = persisted
                    return persisted
        return encoded_id

    def _build_review_keyboard(self, candidate_id: str) -> Any:
        return build_review_keyboard(
            candidate_id,
            encode_candidate_id=self._encode_callback_candidate_id,
        )

    def _resolve_message_thread_id(self, explicit_thread_id: int | None = None) -> int | None:
        if explicit_thread_id is not None:
            return explicit_thread_id
        if self.message_thread_id is not None:
            return self.message_thread_id
        if self._last_operator_thread_id is not None:
            return self._last_operator_thread_id
        return None

    def _thread_for(self, category: str) -> int | None:
        mapping = {
            "review": self.thread_review_id,
            "deploy": self.thread_deploy_id,
            "claim": self.thread_claim_id,
            "ops": self.thread_ops_id,
            "alert": self.thread_alert_id,
        }
        configured = mapping.get(category)
        if configured is not None:
            return configured
        dynamic = self._dynamic_thread_bindings.get(category)
        if dynamic is not None:
            return dynamic

        if category in {"deploy", "alert"}:
            review_thread = mapping.get("review") or self._dynamic_thread_bindings.get("review")
            if review_thread is not None:
                return review_thread
        if category == "claim":
            ops_thread = mapping.get("ops") or self._dynamic_thread_bindings.get("ops")
            if ops_thread is not None:
                return ops_thread
        if category != "ops":
            ops_thread = mapping.get("ops") or self._dynamic_thread_bindings.get("ops")
            if ops_thread is not None:
                return ops_thread
        return self._resolve_message_thread_id()

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
        self.dp.message.register(self._handle_pair, Command("pair"))
        self.dp.message.register(self._handle_help, Command("help"))
        self.dp.message.register(self._handle_status, Command("status"))
        self.dp.message.register(self._handle_control, Command("control"))
        self.dp.message.register(self._handle_queue, Command("queue"))
        self.dp.message.register(self._handle_candidate, Command("candidate"))
        self.dp.message.register(self._handle_deploys, Command("deploys"))
        self.dp.message.register(self._handle_claimfees, Command("claimfees"))
        self.dp.message.register(self._handle_setmode, Command("setmode"))
        self.dp.message.register(self._handle_setbot, Command("setbot"))
        self.dp.message.register(self._handle_setdeployer, Command("setdeployer"))
        self.dp.message.register(self._handle_wallets, Command("wallets"))
        self.dp.message.register(self._handle_setsigner, Command("setsigner"))
        self.dp.message.register(self._handle_setadmin, Command("setadmin"))
        self.dp.message.register(self._handle_setreward, Command("setreward"))
        self.dp.message.register(self._handle_manualdeploy, Command("manualdeploy"))
        self.dp.message.register(self._handle_deploynow, Command("deploynow"))
        self.dp.message.register(self._handle_deployca, Command("deployca"))
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
                BotCommand(command="control", description="Control panel"),
                BotCommand(command="queue", description="Pending review queue"),
                BotCommand(command="candidate", description="Candidate detail by ID"),
                BotCommand(command="deploys", description="Recent deployments"),
                BotCommand(command="claimfees", description="Claim rewards by token"),
                BotCommand(command="setmode", description="Set mode review/auto"),
                BotCommand(command="setbot", description="Set bot on/off"),
                BotCommand(command="setdeployer", description="Set deployer mode"),
                BotCommand(command="wallets", description="Show runtime wallet config"),
                BotCommand(command="setsigner", description="Set deployer signer wallet/key"),
                BotCommand(command="setadmin", description="Set token admin wallet"),
                BotCommand(command="setreward", description="Set reward recipient wallet"),
                BotCommand(command="manualdeploy", description="Manual deploy guide"),
                BotCommand(command="deploynow", description="Manual deploy now"),
                BotCommand(command="deployca", description="Deploy existing candidate"),
                BotCommand(command="help", description="Usage guide"),
                BotCommand(command="pair", description="Pair bot to this chat"),
            ]
        )

    async def _handle_pair(self, message: Message) -> None:
        """Pair bot to current chat for easier setup in groups/forum topics."""
        self.chat_id = str(message.chat.id)
        self._persist_authorized_chat(self.chat_id)
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            "🔗 <b>Paired</b>\n\n"
            f"• <b>Chat ID:</b> {_fmt_inline_code(self.chat_id)}\n"
            "Bot will now accept commands in this chat.",
            parse_mode="HTML",
        )

    # ── command handlers ──────────────────────────────────────────────────────

    async def _handle_start(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            "🤖 <b>Clank&Claw Bot</b>\n\n"
            "Manual review mode is active.\n"
            "Approve/reject is done per candidate from inline buttons.\n\n"
            "<b>Commands:</b>\n"
            "/status — Operational counters\n"
            "/control — Runtime control panel\n"
            "/queue — Pending queue\n"
            "/candidate &lt;id&gt; — Candidate detail\n"
            "/deploys — Recent deployments\n"
            "/claimfees &lt;token_address&gt; — Claim token rewards\n"
            "/setmode &lt;review|auto&gt; — Set review mode\n"
            "/setbot &lt;on|off&gt; — Toggle bot notifications\n"
            "/setdeployer &lt;clanker|bankr|both&gt; — Set deployer mode\n"
            "/wallets — Show runtime wallet config\n"
            "/setsigner &lt;address|private_key|default&gt; — Set deployer signer\n"
            "/setadmin &lt;address|default&gt; — Set token admin\n"
            "/setreward &lt;address|default&gt; — Set reward recipient\n"
            "/manualdeploy — Show manual deploy command format\n"
            "/deploynow &lt;platform&gt; &lt;name&gt; &lt;symbol&gt; &lt;image_or_cid|auto&gt; [description]\n"
            "/deployca &lt;platform&gt; &lt;candidate_id&gt; — Force deploy existing candidate\n"
            "/help — This help",
            parse_mode="HTML",
        )

    async def _handle_help(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            "📚 <b>Help</b>\n\n"
            "<b>Review Flow</b>\n"
            "1. New candidate arrives with ✅/❌ buttons\n"
            "2. Approve → lock + deploy preparation\n"
            "3. Reject → mark rejected immediately\n\n"
            "<b>Commands:</b>\n"
            "/status — Operational counters\n"
            "/control — Runtime control panel\n"
            "/queue — Pending queue\n"
            "/candidate &lt;id&gt; — Candidate detail\n"
            "/deploys — Recent deployments\n"
            "/claimfees &lt;token_address&gt; — Claim rewards via Clanker SDK\n"
            "/setmode &lt;review|auto&gt; — Set review mode\n"
            "/setbot &lt;on|off&gt; — Toggle bot notifications\n"
            "/setdeployer &lt;clanker|bankr|both&gt; — Set deployer mode\n"
            "/wallets — Show runtime wallet config\n"
            "/setsigner &lt;address|private_key|default&gt; — Set deployer signer\n"
            "/setadmin &lt;address|default&gt; — Set token admin\n"
            "/setreward &lt;address|default&gt; — Set reward recipient\n"
            "/manualdeploy — Show manual deploy command format\n"
            "/deploynow &lt;platform&gt; &lt;name&gt; &lt;symbol&gt; &lt;image_or_cid|auto&gt; [description]\n"
            "/deployca &lt;platform&gt; &lt;candidate_id&gt; — Force deploy existing candidate\n"
            "/cancel &lt;id&gt; — Cancel pending review (manual override)\n"
            "/help — Command guide",
            parse_mode="HTML",
        )

    async def _handle_status(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
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
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_pending_reviews()
            msg = build_queue_message(rows)
            await message.answer(msg, parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error listing queue: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching queue.", parse_mode="HTML")

    async def _handle_control(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        mode = (self._runtime_get("ops.mode") or "review").strip().lower()
        bot_state = (self._runtime_get("ops.bot_enabled") or "on").strip().lower()
        deployer = (self._runtime_get("ops.deployer_mode") or "clanker").strip().lower()
        auto_rule = (self._runtime_get("ops.auto_rule") or "priority_review_only").strip().lower()

        mode_view = "🟩 AUTO" if mode == "auto" else "🟦 REVIEW"
        bot_view = "🟢 ON" if bot_state in {"on", "true", "1", "yes"} else "🔴 OFF"
        deployer_view = deployer.upper()
        if deployer == "both":
            deployer_view = "BOTH (planned)"
        if deployer == "bankr":
            deployer_view = "BANKR (planned)"

        await message.answer(
            "🎛️ <b>Runtime Control</b>\n\n"
            f"• <b>Mode:</b> {mode_view}\n"
            f"• <b>Bot:</b> {bot_view}\n"
            f"• <b>Deployer:</b> {_fmt_text(deployer_view)}\n"
            f"• <b>Auto rule:</b> {_fmt_inline_code(auto_rule)}\n\n"
            "Setters:\n"
            "• <code>/setmode review</code> or <code>/setmode auto</code>\n"
            "• <code>/setbot on</code> or <code>/setbot off</code>\n"
            "• <code>/setdeployer clanker|bankr|both</code>",
            parse_mode="HTML",
        )

    async def _handle_setmode(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setmode &lt;review|auto&gt;", parse_mode="HTML")
            return
        mode = parts[1].strip().lower()
        if mode not in {"review", "auto"}:
            await message.answer("⚠️ Invalid mode. Use review or auto.", parse_mode="HTML")
            return
        if not self._runtime_set("ops.mode", mode):
            await message.answer("⚠️ Failed saving mode.", parse_mode="HTML")
            return
        await message.answer(f"✅ Mode set to <b>{_fmt_text(mode)}</b>.", parse_mode="HTML")

    async def _handle_setbot(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setbot &lt;on|off&gt;", parse_mode="HTML")
            return
        value = parts[1].strip().lower()
        if value not in {"on", "off"}:
            await message.answer("⚠️ Invalid value. Use on or off.", parse_mode="HTML")
            return
        if not self._runtime_set("ops.bot_enabled", value):
            await message.answer("⚠️ Failed saving bot state.", parse_mode="HTML")
            return
        await message.answer(f"✅ Bot notifications set to <b>{_fmt_text(value)}</b>.", parse_mode="HTML")

    async def _handle_setdeployer(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setdeployer &lt;clanker|bankr|both&gt;", parse_mode="HTML")
            return
        value = parts[1].strip().lower()
        if value not in {"clanker", "bankr", "both"}:
            await message.answer("⚠️ Invalid deployer mode.", parse_mode="HTML")
            return
        if not self._runtime_set("ops.deployer_mode", value):
            await message.answer("⚠️ Failed saving deployer mode.", parse_mode="HTML")
            return
        note = ""
        if value in {"bankr", "both"}:
            note = "\n⚠️ Bankr execution is not implemented yet; runtime will fallback to clanker."
        await message.answer(
            f"✅ Deployer mode set to <b>{_fmt_text(value)}</b>.{note}",
            parse_mode="HTML",
        )

    async def _handle_candidate(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
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
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer("ℹ️ Database not available.", parse_mode="HTML")
            return
        try:
            rows = self._db.list_recent_deployments(limit=10)
            msg = build_deploys_message(rows)
            await message.answer(msg, parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Error fetching deployments: {exc}", exc_info=True)
            await message.answer("⚠️ Error fetching deployments.", parse_mode="HTML")

    async def _handle_cancel(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
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
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("claim", thread_id)
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
                claim_msg = (
                    "💸 <b>Claim Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> success\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}{tx_line}"
                )
                await message.answer(claim_msg, parse_mode="HTML")
            else:
                claim_msg = (
                    "💸 <b>Claim Result</b>\n\n"
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> failed\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}\n"
                    f"• <b>Error:</b> {_fmt_text(result.error_code or 'unknown')}\n"
                    f"• <b>Message:</b> {_fmt_text(result.error_message or 'unknown')}"
                )
                await message.answer(claim_msg, parse_mode="HTML")
        except Exception as exc:
            logger.error("Error in claim fees handler: %s", exc, exc_info=True)
            await message.answer("⚠️ Claim fees execution failed.", parse_mode="HTML")

    def _runtime_get(self, key: str) -> str | None:
        if not self._db or not hasattr(self._db, "get_runtime_setting"):
            return None
        try:
            return self._db.get_runtime_setting(key)
        except Exception as exc:
            logger.error("Failed reading runtime setting %s: %s", key, exc, exc_info=True)
            return None

    def _runtime_set(self, key: str, value: str) -> bool:
        if not self._db or not hasattr(self._db, "set_runtime_setting"):
            return False
        try:
            self._db.set_runtime_setting(key, value)
            return True
        except Exception as exc:
            logger.error("Failed writing runtime setting %s: %s", key, exc, exc_info=True)
            return False

    def _runtime_delete(self, key: str) -> bool:
        if not self._db or not hasattr(self._db, "delete_runtime_setting"):
            return False
        try:
            self._db.delete_runtime_setting(key)
            return True
        except Exception as exc:
            logger.error("Failed deleting runtime setting %s: %s", key, exc, exc_info=True)
            return False

    async def _handle_wallets(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        signer_runtime = self._runtime_get("wallet.deployer_signer")
        admin_runtime = self._runtime_get("wallet.token_admin")
        reward_runtime = self._runtime_get("wallet.fee_recipient")

        signer_display = _mask_sensitive_wallet(signer_runtime) if signer_runtime else "default (env/config)"
        admin_display = admin_runtime or "default (env/config)"
        reward_display = reward_runtime or "default (env/config)"

        await message.answer(
            "👛 <b>Runtime Wallet Config</b>\n\n"
            f"• <b>Signer/Deployer:</b> {_fmt_inline_code(signer_display)}\n"
            f"• <b>Token Admin:</b> {_fmt_inline_code(admin_display)}\n"
            f"• <b>Reward Recipient:</b> {_fmt_inline_code(reward_display)}\n\n"
            "Use /setsigner, /setadmin, /setreward to update.\n"
            "Use value <code>default</code> to clear override.",
            parse_mode="HTML",
        )

    async def _handle_setsigner(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setsigner &lt;address|private_key|default&gt;", parse_mode="HTML")
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.deployer_signer"):
                await message.answer("⚠️ Failed resetting signer override.", parse_mode="HTML")
                return
            await message.answer("✅ Signer override reset to default (env/config).", parse_mode="HTML")
            return
        if not (_is_evm_address(value) or _is_private_key(value)):
            await message.answer("⚠️ Invalid signer. Use EVM address or 0x private key.", parse_mode="HTML")
            return
        if not self._runtime_set("wallet.deployer_signer", value):
            await message.answer("⚠️ Failed saving signer override.", parse_mode="HTML")
            return
        await message.answer(
            f"✅ Signer override updated: {_fmt_inline_code(_mask_sensitive_wallet(value))}",
            parse_mode="HTML",
        )

    async def _handle_setadmin(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setadmin &lt;address|default&gt;", parse_mode="HTML")
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.token_admin"):
                await message.answer("⚠️ Failed resetting token admin override.", parse_mode="HTML")
                return
            await message.answer("✅ Token admin override reset to default (env/config).", parse_mode="HTML")
            return
        if not _is_evm_address(value):
            await message.answer("⚠️ Invalid token admin address.", parse_mode="HTML")
            return
        if not self._runtime_set("wallet.token_admin", value):
            await message.answer("⚠️ Failed saving token admin override.", parse_mode="HTML")
            return
        await message.answer(f"✅ Token admin updated: {_fmt_inline_code(value)}", parse_mode="HTML")

    async def _handle_setreward(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Usage: /setreward &lt;address|default&gt;", parse_mode="HTML")
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.fee_recipient"):
                await message.answer("⚠️ Failed resetting reward recipient override.", parse_mode="HTML")
                return
            await message.answer("✅ Reward recipient override reset to default (env/config).", parse_mode="HTML")
            return
        if not _is_evm_address(value):
            await message.answer("⚠️ Invalid reward recipient address.", parse_mode="HTML")
            return
        if not self._runtime_set("wallet.fee_recipient", value):
            await message.answer("⚠️ Failed saving reward recipient override.", parse_mode="HTML")
            return
        await message.answer(f"✅ Reward recipient updated: {_fmt_inline_code(value)}", parse_mode="HTML")

    async def _handle_manualdeploy(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            "🧪 <b>Manual Deploy Guide</b>\n\n"
            "<b>Direct Deploy:</b>\n"
            "<code>/deploynow clanker \"Token Name\" SYMBOL auto optional description</code>\n"
            "<code>/deploynow clanker \"Token Name\" SYMBOL ipfs://QmCID optional description</code>\n"
            "<code>/deploynow clanker \"Token Name\" SYMBOL https://example.com/logo.png optional description</code>\n\n"
            "<b>Deploy Existing Candidate:</b>\n"
            "<code>/deployca clanker &lt;candidate_id&gt;</code>\n\n"
            "Notes:\n"
            "• Current executable platform: <b>clanker</b>\n"
            "• <b>bankr</b>/<b>both</b> are reserved and will be rejected\n"
            "• Name with spaces must use quotes",
            parse_mode="HTML",
        )

    async def _handle_deploynow(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        self._bind_dynamic_thread("deploy", thread_id)
        self._bind_dynamic_thread("alert", thread_id)
        if not self.on_manual_deploy:
            await message.answer("⚠️ Manual deploy handler is not configured.", parse_mode="HTML")
            return

        args = _parse_command_args(message.text or "")
        if len(args) < 4:
            await message.answer(
                "Usage: /deploynow &lt;platform&gt; &lt;name&gt; &lt;symbol&gt; &lt;image_or_cid|auto&gt; [description]",
                parse_mode="HTML",
            )
            return
        platform, token_name, token_symbol, image_ref = args[0], args[1], args[2], args[3]
        description = " ".join(args[4:]).strip() if len(args) > 4 else ""

        if platform.strip().lower() not in {"clanker", "bankr", "both"}:
            await message.answer("⚠️ Invalid platform. Use clanker|bankr|both.", parse_mode="HTML")
            return

        if len(token_name.strip()) < 2 or len(token_symbol.strip()) < 2:
            await message.answer("⚠️ Token name/symbol too short.", parse_mode="HTML")
            return

        await message.answer("⏳ Manual deploy started…", parse_mode="HTML")
        try:
            result = await self.on_manual_deploy(
                platform.strip().lower(),
                token_name.strip(),
                token_symbol.strip(),
                image_ref.strip(),
                description,
                {
                    "chat_id": message.chat.id,
                    "user_id": getattr(message.from_user, "id", None),
                    "username": getattr(message.from_user, "username", None),
                    "thread_id": thread_id,
                },
            )
            candidate_id = result.get("candidate_id") or "unknown"
            success = bool(result.get("success"))
            if success:
                await message.answer(
                    "✅ <b>Manual Deploy Success</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "❌ <b>Manual Deploy Failed</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.error("Error in deploynow handler: %s", exc, exc_info=True)
            await message.answer(
                "⚠️ Manual deploy rejected.\n"
                f"Reason: {_fmt_text(str(exc))}",
                parse_mode="HTML",
            )

    async def _handle_deployca(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        self._bind_dynamic_thread("deploy", thread_id)
        self._bind_dynamic_thread("alert", thread_id)
        if not self.on_manual_deploy_candidate:
            await message.answer("⚠️ Manual candidate deploy handler is not configured.", parse_mode="HTML")
            return

        args = _parse_command_args(message.text or "")
        if len(args) < 2:
            await message.answer("Usage: /deployca &lt;platform&gt; &lt;candidate_id&gt;", parse_mode="HTML")
            return
        platform, candidate_id = args[0], args[1]
        if platform.strip().lower() not in {"clanker", "bankr", "both"}:
            await message.answer("⚠️ Invalid platform. Use clanker|bankr|both.", parse_mode="HTML")
            return

        await message.answer(
            f"⏳ Deploying candidate {_fmt_inline_code(candidate_id)}…",
            parse_mode="HTML",
        )
        try:
            result = await self.on_manual_deploy_candidate(
                platform.strip().lower(),
                candidate_id.strip(),
                {
                    "chat_id": message.chat.id,
                    "user_id": getattr(message.from_user, "id", None),
                    "username": getattr(message.from_user, "username", None),
                    "thread_id": thread_id,
                },
            )
            success = bool(result.get("success"))
            if success:
                await message.answer(
                    "✅ <b>Deploy Candidate Success</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "❌ <b>Deploy Candidate Failed</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                )
        except Exception as exc:
            logger.error("Error in deployca handler: %s", exc, exc_info=True)
            await message.answer(
                "⚠️ Deploy candidate rejected.\n"
                f"Reason: {_fmt_text(str(exc))}",
                parse_mode="HTML",
            )

    # ── callback handlers ─────────────────────────────────────────────────────

    async def _handle_approve(self, callback: CallbackQuery) -> None:
        if not callback.data:
            return
        if not callback.message or not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
        self._bind_dynamic_thread("deploy", thread_id)
        self._bind_dynamic_thread("alert", thread_id)

        encoded_candidate_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_candidate_id)
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
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)

        encoded_candidate_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_candidate_id)
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
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
        encoded_candidate_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_candidate_id)
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
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
        encoded_candidate_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_candidate_id)
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
                reply_markup=self._build_review_keyboard(candidate_id),
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
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
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
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
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
            keyboard = self._build_review_keyboard(candidate_id)

            result = await self._send_bot_message(
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=True,
                message_thread_id=self._thread_for("review"),
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
                message_thread_id=self._thread_for("deploy"),
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
                message_thread_id=self._thread_for("deploy"),
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
                message_thread_id=self._thread_for("alert"),
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
