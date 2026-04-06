"""Telegram bot for approval flow."""

import asyncio
import secrets
import html
import json
import logging
import os
import re
import shlex
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command, StateFilter
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
    from clankandclaw.utils.llm import enrich_signal_with_llm, suggest_token_metadata, suggest_token_description
    from clankandclaw.telegram.formatters import (
        _fmt_text, _fmt_inline_code, _fmt_dashboard_header, _source_label, _network_icon
    )
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
    FSMContext = Any  # type: ignore
    State = Any  # type: ignore
    StatesGroup = Any  # type: ignore

logger = logging.getLogger(__name__)

_MAX_RAW_TEXT = 300  # chars shown in review message
_MAX_QUEUE_ITEMS = 10
_MAX_ERROR_TEXT = 80
_MAX_REASONS = 6
_MAX_CALLBACK_DATA = 64
_THREAD_CATEGORIES = ("review", "deploy", "claim", "ops", "alert")
_DEFAULT_FORUM_TOPIC_TITLES: dict[str, str] = {
    "review": "cnc-review",
    "deploy": "cnc-deploy",
    "claim": "cnc-claim",
    "ops": "cnc-ops",
    "alert": "cnc-alert",
}
_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


class ManualDeployStates(StatesGroup):
    """FSM states for interactive manual deployment."""
    platform = State()
    name = State()
    symbol = State()
    image = State()
    description = State()
    confirm = State()


# UI Helpers imported from clankandclaw.telegram.formatters


def _fmt_num(value: Any, *, digits: int = 0, fallback: str = "n/a") -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if digits <= 0:
        return f"{int(num):,}"
    return f"{num:,.{digits}f}"


def _build_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Master Navigation Helper."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Status", callback_data="nav_status"),
                InlineKeyboardButton(text="📥 Queue", callback_data="nav_queue"),
            ],
            [
                InlineKeyboardButton(text="🛠 Tools", callback_data="nav_tools"),
                InlineKeyboardButton(text="📂 History", callback_data="nav_deploys"),
            ],
            [
                InlineKeyboardButton(text="🧪 Manual Deploy", callback_data="nav_wizard"),
                InlineKeyboardButton(text="❓ Help", callback_data="nav_help"),
            ],
        ]
    )


def _build_tools_keyboard() -> InlineKeyboardMarkup:
    """Categorized Action Hub."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Mode", callback_data="nav_tools_mode"),
                InlineKeyboardButton(text="🤖 Bot", callback_data="nav_tools_bot"),
                InlineKeyboardButton(text="🏗 Deployer", callback_data="nav_tools_plat"),
            ],
            [
                InlineKeyboardButton(text="💸 Claim", callback_data="nav_tools_claim"),
                InlineKeyboardButton(text="🔐 Wallets", callback_data="nav_tools_wallets"),
            ],
            [
                InlineKeyboardButton(text="📍 Pair Current", callback_data="nav_tools_pair"),
                InlineKeyboardButton(text="⚡ Autothread", callback_data="nav_tools_auto"),
            ],
            [
                InlineKeyboardButton(text="↩️ Dashboard", callback_data="nav_control"),
            ]
        ]
    )


def _build_category_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Generic category picker for pairing/binding."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📑 Review", callback_data=f"{prefix}:review"),
                InlineKeyboardButton(text="🚀 Deploy", callback_data=f"{prefix}:deploy"),
            ],
            [
                InlineKeyboardButton(text="💸 Claim", callback_data=f"{prefix}:claim"),
                InlineKeyboardButton(text="⚙️ Ops", callback_data=f"{prefix}:ops"),
            ],
            [
                InlineKeyboardButton(text="🚨 Alert", callback_data=f"{prefix}:alert"),
            ],
            [
                InlineKeyboardButton(text="↩️ Tools", callback_data="nav_tools"),
            ]
        ]
    )


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


def build_forum_topic_plan(existing_thread_bindings: dict[str, int] | None = None) -> list[tuple[str, str]]:
    """Return categories/topics that still need to be created for forum setup."""
    existing_thread_bindings = existing_thread_bindings or {}
    plan: list[tuple[str, str]] = []
    for category in _THREAD_CATEGORIES:
        title = _DEFAULT_FORUM_TOPIC_TITLES.get(category, f"cnc-{category}")
        existing = existing_thread_bindings.get(category)
        if isinstance(existing, int) and existing > 0:
            continue
        plan.append((category, title))
    return plan


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
    dex_id = _fmt_text(metadata.get("dex_id"), fallback="unknown")
    confidence_tier = _fmt_text(metadata.get("confidence_tier"), fallback="n/a")
    gate_stage = _fmt_text(metadata.get("gate_stage"), fallback="n/a")
    liquidity_usd = _fmt_num(metadata.get("liquidity_usd"), digits=2, fallback="0.00")
    volume = metadata.get("volume") or {}
    tx_data = metadata.get("transactions") or {}
    
    volume_m1 = _fmt_num(volume.get("m1"), digits=2, fallback="0.00")
    volume_m5 = _fmt_num(volume.get("m5"), digits=2, fallback="0.00")
    volume_m15 = _fmt_num(volume.get("m15"), digits=2, fallback="0.00")
    volume_h1 = _fmt_num(volume.get("h1"), digits=2, fallback="0.00")
    
    tx_m1 = _fmt_num(tx_data.get("m1"), fallback="0")
    tx_m5 = _fmt_num(tx_data.get("m5"), fallback="0")
    tx_h1 = _fmt_num(tx_data.get("h1"), fallback="0")
    contracts = [*list(metadata.get("evm_contracts") or []), *list(metadata.get("sol_contracts") or [])]
    contracts = [str(item) for item in contracts if str(item).strip()]
    contract_hint = ", ".join(_fmt_inline_code(item) for item in contracts[:2]) if contracts else "n/a"
    if len(contracts) > 2:
        contract_hint += f" (+{len(contracts) - 2})"

    net_icon = _network_icon(network)

    lines = [
        f"{priority_emoji} {net_icon} <b>Review:</b> {network.upper()} | {dex_id.upper()}",
        f"<b>ID:</b> {_fmt_inline_code(candidate_id)} | <b>Score:</b> {_fmt_num(score)}",
        f"<b>Pri:</b> {_fmt_text(review_priority)} | <b>Conf:</b> {confidence_tier}",
    ]
    
    is_trending = network.lower() in ("solana", "bsc", "sol")
    if is_trending:
        lines.append(f"<b>Vol h1:</b> ${volume_h1} | <b>Liq:</b> ${liquidity_usd} | <b>Tx h1:</b> {tx_h1}")
    else:
        lines.append(f"<b>Vol:</b> m5 ${volume_m5} | m15 ${volume_m15} | <b>Liq:</b> ${liquidity_usd}")
        lines.append(f"<b>Tx:</b> m5 {tx_m5}")
        
    lines.append(f"<b>Contract:</b> {contract_hint} | <b>Gate:</b> {gate_stage}")
    lines.append(f"<b>Signals:</b> {_fmt_text(reasons, fallback='—')}")

    if context_url:
        safe_url = html.escape(context_url, quote=True)
        lines.append(f'• <b>Source:</b> <a href="{safe_url}">{_source_label(source)}</a>' + (f" (@{_fmt_text(author_handle)})" if author_handle else ""))

    # AI Insight Block
    if metadata.get("ai_enriched"):
        bullish = metadata.get("ai_bullish_score", 0)
        rationale = metadata.get("ai_rationale", "No rationale provided.")
        mood = "💎" if bullish >= 80 else "🔥" if bullish >= 60 else "⚖️" if bullish >= 40 else "⚠️"
        lines.append(f"\n{mood} <b>AI INSIGHT | {bullish}% BULLISH</b>")
        lines.append(f"<i>{_fmt_text(rationale)}</i>")

    if raw_text:
        trimmed = _shorten_text(raw_text, 60)
        lines += ["", f"<blockquote>{_fmt_text(trimmed)}</blockquote>"]

    return "\n".join(lines)


def build_queue_message(rows: list[Any]) -> str:
    """Build compact queue message from pending-review rows."""
    if not rows:
        return "📭 No pending reviews."

    lines = [f"Total: <b>{len(rows)}</b>", ""]
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

    lines = [f"Total: <b>{len(rows)}</b>", ""]
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
    mode: str = "summary",
) -> Any:
    """Build inline keyboard for operator actions."""
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    if mode == "detail":
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
                        text="⬅️ Summary",
                        callback_data=build_action_callback_data(
                            "refresh",
                            candidate_id,
                            encode_candidate_id=encode_candidate_id,
                        ),
                    ),
                    InlineKeyboardButton(
                        text="🔄 Refresh",
                        callback_data=build_action_callback_data(
                            "refresh_detail",
                            candidate_id,
                            encode_candidate_id=encode_candidate_id,
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🧪 Edit & Deploy",
                        callback_data=build_action_callback_data(
                            "wiz_edit",
                            candidate_id,
                            encode_candidate_id=encode_candidate_id,
                        ),
                    ),
                ],
            ]
        )

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
                InlineKeyboardButton(
                    text="🧪 Edit & Deploy",
                    callback_data=build_action_callback_data(
                        "wiz_edit",
                        candidate_id,
                        encode_candidate_id=encode_candidate_id,
                    ),
                ),
            ]
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
        pinata_client: Any = None,
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
        self._pinata = pinata_client
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
        """Resolve the target thread for a category with strict priority and fallback."""
        if not self.chat_id:
             return None
             
        mapping = {
            "review": self.thread_review_id,
            "deploy": self.thread_deploy_id,
            "claim": self.thread_claim_id,
            "ops": self.thread_ops_id,
            "alert": self.thread_alert_id,
        }
        
        # 1. Configured Env (Priority)
        configured = mapping.get(category)
        if configured is not None:
             return configured
             
        # 2. Dynamic Runtime Setting (DB)
        dynamic = self._dynamic_thread_bindings.get(category)
        if dynamic is not None:
             return dynamic
             
        # 3. Categorical Fallbacks (Logic)
        if category == "alert":
             # Alerts fallback to the review area
             return mapping.get("review") or self._dynamic_thread_bindings.get("review")
        
        if category in ("deploy", "claim"):
             # Deploys/Claims fallback to the ops area
             return mapping.get("ops") or self._dynamic_thread_bindings.get("ops")
             
        # 4. Global Ops (General Area)
        if category != "ops":
             ops_thread = mapping.get("ops") or self._dynamic_thread_bindings.get("ops")
             if ops_thread is not None:
                  return ops_thread
                  
        # 5. Default Thread / Chat Root
        return self._resolve_message_thread_id()

    async def _send_bot_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Any = None,
        disable_web_page_preview: bool = True,
        message_thread_id: int | None = None,
    ) -> Any:
        """Shared message sender with robustness guards."""
        if not self.chat_id:
             logger.warning("Attempted to send message, but chat_id is missing.")
             return None
             
        try:
            return await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
                message_thread_id=message_thread_id,
            )
        except Exception as exc:
            logger.error(f"Telegram API call failed: {exc}", exc_info=True)
            return None

    def _setup_handlers(self) -> None:
        """Setup message and callback handlers."""
        self.dp.message.register(self._handle_start, Command("start"))
        self.dp.message.register(self._handle_pair, Command("pair"))
        self.dp.message.register(self._handle_autothread, Command("autothread"))
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
        self.dp.message.register(self._handle_cancel, Command("cancel"))
        self.dp.message.register(self._handle_setthreshold, Command("setthreshold"))
        self.dp.message.register(self._handle_panic, Command("panic"))
        self.dp.callback_query.register(self._handle_approve, F.data.startswith("approve:"))
        self.dp.callback_query.register(self._handle_reject, F.data.startswith("reject:"))
        self.dp.callback_query.register(self._handle_detail, F.data.startswith("detail:"))
        self.dp.callback_query.register(self._handle_refresh, F.data.startswith("refresh:"))
        self.dp.callback_query.register(self._handle_detail, F.data.startswith("refresh_detail:"))
        # Master Navigation Callbacks
        self.dp.callback_query.register(self._handle_nav_status, F.data == "nav_status")
        self.dp.callback_query.register(self._handle_nav_queue, F.data == "nav_queue")
        self.dp.callback_query.register(self._handle_nav_deploys, F.data == "nav_deploys")
        self.dp.callback_query.register(self._handle_nav_control, F.data == "nav_control")
        self.dp.callback_query.register(self._handle_nav_tools, F.data == "nav_tools")
        self.dp.callback_query.register(self._handle_nav_tools_mode, F.data == "nav_tools_mode")
        self.dp.callback_query.register(self._handle_nav_tools_bot, F.data == "nav_tools_bot")
        self.dp.callback_query.register(self._handle_nav_tools_plat, F.data == "nav_tools_plat")
        self.dp.callback_query.register(self._handle_nav_tools_claim, F.data == "nav_tools_claim")
        self.dp.callback_query.register(self._handle_nav_tools_wallets, F.data == "nav_tools_wallets")
        self.dp.callback_query.register(self._handle_nav_tools_pair, F.data == "nav_tools_pair")
        self.dp.callback_query.register(self._handle_nav_tools_auto, F.data == "nav_tools_auto")
        self.dp.callback_query.register(self._handle_exec_pair, F.data.startswith("exec_pair:"))
        self.dp.callback_query.register(self._handle_exec_setmode, F.data.startswith("exec_mode:"))
        self.dp.callback_query.register(self._handle_exec_setbot, F.data.startswith("exec_bot:"))
        self.dp.callback_query.register(self._handle_exec_setplat, F.data.startswith("exec_plat:"))
        self.dp.callback_query.register(self._handle_exec_claim, F.data.startswith("exec_claim:"))
        self.dp.callback_query.register(self._handle_nav_help, F.data == "nav_help")
        # Wizard Handlers
        self.dp.callback_query.register(self._handle_nav_wizard, F.data == "nav_wizard")
        self.dp.callback_query.register(self._handle_wizard_platform, ManualDeployStates.platform, F.data.startswith("wiz_plat:"))
        self.dp.message.register(self._handle_wizard_name, ManualDeployStates.name)
        self.dp.message.register(self._handle_wizard_symbol, ManualDeployStates.symbol)
        self.dp.message.register(self._handle_wizard_image, ManualDeployStates.image)
        self.dp.callback_query.register(self._handle_wizard_image_auto, ManualDeployStates.image, F.data == "wiz_img:auto")
        self.dp.message.register(self._handle_wizard_description, ManualDeployStates.description)
        self.dp.callback_query.register(self._handle_wizard_description_skip, ManualDeployStates.description, F.data == "wiz_desc:skip")
        self.dp.callback_query.register(self._handle_wizard_confirm, ManualDeployStates.confirm, F.data == "wiz_confirm")
        self.dp.callback_query.register(self._handle_wizard_edit, F.data.startswith("wiz_edit:"))
        self.dp.callback_query.register(self._handle_wizard_back, F.data == "wiz_back")
        self.dp.callback_query.register(self._handle_wizard_suggest, F.data == "wiz_suggest")
        self.dp.callback_query.register(self._handle_wizard_desc_suggest, F.data == "wiz_desc_suggest")
        self.dp.callback_query.register(self._handle_wizard_apply_suggest, F.data.startswith("wiz_apply_suggest:"))
        self.dp.callback_query.register(self._handle_wizard_cancel, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.platform, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.name, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.symbol, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.image, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.description, F.data == "wiz_cancel")
        self.dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.confirm, F.data == "wiz_cancel")

    def _is_authorized_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.chat_id)

    async def _set_bot_commands(self) -> None:
        """Publish compact slash menu to Telegram."""
        await self.bot.set_my_commands(
            [
                BotCommand(command="wallets", description="Show runtime wallet config"),
                BotCommand(command="setsigner", description="Set deployer signer wallet/key"),
                BotCommand(command="setadmin", description="Set token admin wallet"),
                BotCommand(command="setreward", description="Set reward recipient wallet"),
                BotCommand(command="manualdeploy", description="Manual deploy guide"),
                BotCommand(command="deploynow", description="Manual deploy now"),
                BotCommand(command="deployca", description="Deploy existing candidate"),
                BotCommand(command="help", description="Usage guide"),
                BotCommand(command="pair", description="Pair bot to this chat"),
                BotCommand(command="autothread", description="Auto-create forum topics"),
                BotCommand(command="setthreshold", description="Set auto-deploy score threshold (0-100)"),
                BotCommand(command="panic", description="EMERGENCY: Switch to review mode immediately"),
            ]
        )

    async def _ensure_forum_topics_bound(self) -> tuple[list[str], list[str]]:
        """Create forum topics when needed and bind thread IDs to runtime settings."""
        chat_id = self.chat_id
        if not chat_id:
            return [], ["chat is not configured"]

        failures: list[str] = []
        created: list[str] = []
        try:
            chat = await self.bot.get_chat(chat_id)
        except Exception as exc:
            return [], [f"cannot fetch chat metadata: {exc}"]

        if str(getattr(chat, "type", "")) != "supergroup" or not bool(getattr(chat, "is_forum", False)):
            return [], ["paired chat is not a forum supergroup"]

        current_bindings = dict(self._dynamic_thread_bindings)
        for category, topic_title in build_forum_topic_plan(current_bindings):
            try:
                # Smart discovery: If we already have an ID, try to 'adapt' it (rename instead of create)
                existing_id = current_bindings.get(category)
                if existing_id:
                    try:
                        await self.bot.edit_forum_topic(
                            chat_id=chat_id,
                            message_thread_id=existing_id,
                            name=topic_title,
                        )
                        created.append(f"{category}:{existing_id}")
                        continue
                    except Exception:
                        # If edit fails, it might be deleted. Fall through to create.
                        logger.debug(f"Failed to edit topic {existing_id} for {category}, will recreate.")

                # If no ID or topic missing, create new
                forum_topic = await self.bot.create_forum_topic(
                    chat_id=chat_id,
                    name=topic_title,
                )
                thread_id = int(getattr(forum_topic, "message_thread_id", 0) or 0)
                if thread_id <= 0:
                    failures.append(f"{category}: topic created but no thread id returned")
                    continue
                self._bind_dynamic_thread(category, thread_id)
                current_bindings[category] = thread_id
                created.append(f"{category}:{thread_id}")
            except Exception as exc:
                failures.append(f"{category}: {exc}")

        return created, failures

    async def _handle_pair(self, message: Message) -> None:
        """Pair bot to current chat or bind current thread to a specific category."""
        self.chat_id = str(message.chat.id)
        self._persist_authorized_chat(self.chat_id)
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        
        args = (message.text or "").strip().split()
        category = args[1].lower() if len(args) > 1 else None
        
        if category:
             if category not in _THREAD_CATEGORIES:
                  await message.answer(
                       _fmt_dashboard_header("Notice", "⚠️") +
                       f"Invalid category. Use: {', '.join(_THREAD_CATEGORIES)}", 
                       parse_mode="HTML",
                       reply_markup=_build_dashboard_keyboard()
                  )
                  return
             
             if not thread_id:
                  await message.answer(
                       _fmt_dashboard_header("Notice", "⚠️") +
                       "Run <code>/pair &lt;cat&gt;</code> inside a topic.", 
                       parse_mode="HTML",
                       reply_markup=_build_dashboard_keyboard()
                  )
                  return
                  
             # Bind and Rename (Smart Adaptation)
             self._bind_dynamic_thread(category, thread_id)
             topic_title = _DEFAULT_FORUM_TOPIC_TITLES.get(category, f"cnc-{category}")
             try:
                  await self.bot.edit_forum_topic(chat_id=self.chat_id, message_thread_id=thread_id, name=topic_title)
                  await message.answer(
                       _fmt_dashboard_header("Success", "✅") +
                       f"Bound and renamed topic to <b>{topic_title}</b>.", 
                       parse_mode="HTML",
                       reply_markup=_build_dashboard_keyboard()
                  )
             except Exception as exc:
                  logger.warning(f"Failed renaming topic {thread_id} to {topic_title}: {exc}")
                  await message.answer(
                       _fmt_dashboard_header("Success", "✅") +
                       f"Bound to <b>{category}</b> (Rename failed - check permissions)", 
                       parse_mode="HTML",
                       reply_markup=_build_dashboard_keyboard()
                  )
             return

        # Regular pairing (Ops binding)
        self._bind_dynamic_thread("ops", thread_id)
        created, failures = await self._ensure_forum_topics_bound()
        created_line = ", ".join(created) if created else "none"
        if failures:
            failures_text = "\n".join(f"• {_fmt_text(item)}" for item in failures[:5])
            failure_block = (
                "\n\n<b>Auto Thread Setup:</b> partial/failed\n"
                f"{failures_text}\n"
                "Tip: grant bot admin permission to <b>Manage Topics</b>, then run <code>/autothread</code>."
            )
        else:
            failure_block = "\n\n<b>Auto Thread Setup:</b> done"
        await message.answer(
            _fmt_dashboard_header("Paired", "🔗") +
            f"• <b>Chat ID:</b> {_fmt_inline_code(self.chat_id)}\n"
            f"• <b>Topics created:</b> {_fmt_text(created_line)}\n"
            "Bot will now accept commands in this chat."
            f"{failure_block}",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard(),
        )

    async def _handle_autothread(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return

        args = (message.text or "").strip().split()
        force = len(args) > 1 and args[1].lower() == "force"
        current_bindings = dict(self._dynamic_thread_bindings)

        if not current_bindings and not force:
            await message.answer(
                _fmt_dashboard_header("Notice", "⚠️") +
                "I detected <b>no existing topics</b> in my database memory.\n\n"
                "If you <b>already have topics</b> from a previous setup, do NOT use this command yet, as it will create duplicates. Instead, go inside each existing topic and send: <code>/pair review</code>, <code>/pair deploy</code>, etc.\n\n"
                "If this is a <b>fresh setup</b> or you want to generate all missing topics automatically, run:\n"
                "<code>/autothread force</code>",
                parse_mode="HTML"
            )
            return

        created, failures = await self._ensure_forum_topics_bound()
        if failures:
            await message.answer(
                _fmt_dashboard_header("Auto Thread Setup Incomplete", "⚠️") +
                f"• <b>Created:</b> {_fmt_text(', '.join(created) if created else 'none')}\n"
                f"• <b>Errors:</b> {_fmt_text(' | '.join(failures[:5]))}\n\n"
                "Ensure bot has admin permission <b>Manage Topics</b>.",
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard(),
            )
            return
        await message.answer(
            _fmt_dashboard_header("Auto Thread Setup Complete", "✅") +
            f"• <b>Created:</b> {_fmt_text(', '.join(created) if created else 'none')}",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard(),
        )

    # ── command handlers ──────────────────────────────────────────────────────

    async def _handle_start(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        
        if not self.chat_id: 
             return
             
        await self.bot.send_message(
             chat_id=self.chat_id,
             text=(
                  _fmt_dashboard_header("Welcome Operator", "👋") +
                  "Bot initialized and paired.\n"
                  "• Use <code>/pair &lt;cat&gt;</code> to custom bind topics.\n"
                  "• Run <code>/status</code> for dashboard health.\n"
                  "• Run <code>/help</code> for manual instructions."
             ),
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard(),
             message_thread_id=thread_id,
        )

    async def _handle_help(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
             return
        await message.answer(
            _fmt_dashboard_header("Command Center", "🛰") +
             "<b>Operational Flow</b>\n"
             "• Click <b>Approval/Reject</b> on signals to execute.\n"
             "• Use <b>🧪 Edit & Deploy</b> to customize metadata.\n"
             "• Use <b>🛠 Tools</b> for advanced system configuration.\n\n"
             "<b>Quick Commands</b>\n"
             "• <code>/status</code>: Real-time bot health.\n"
             "• <code>/queue</code>: Pending review items.\n"
             "• <code>/manualdeploy</code>: Launch the Wizard.",
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard(),
        )

    async def _handle_status(self, message: Message, state: FSMContext | None = None) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        if state:
            await state.clear()
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if self._db:
            try:
                stats = self._db.get_stats()
                await message.answer(
                    _fmt_dashboard_header("Bot Status", "📊") +
                    f"Pending reviews: <b>{stats['pending_reviews']}</b>\n"
                    f"Total candidates seen: <b>{stats['total_candidates']}</b>\n"
                    f"Deployed: <b>{stats['deployed']}</b>\n"
                    f"Deploy failures: <b>{stats['deploy_failed']}</b>\n"
                    f"Rejected: <b>{stats['rejected']}</b>\n\n"
                    f"📂 <b>Binding Map:</b>\n"
                    + "\n".join([f"• {c.capitalize()}: {self._dynamic_thread_bindings.get(c, '—')}" for c in _THREAD_CATEGORIES]),
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard(),
                )
                return
            except Exception as exc:
                logger.error(f"Error fetching status: {exc}", exc_info=True)

        await message.answer(
             _fmt_dashboard_header("Bot Status", "✅") +
             "Status: Running", 
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_queue(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer(
                 _fmt_dashboard_header("Notice", "ℹ️") +
                 "Database not available.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        try:
            rows = self._db.list_pending_reviews()
            msg = build_queue_message(rows)
            await message.answer(
                _fmt_dashboard_header("Pending Queue", "📥") + msg,
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard(),
            )
        except Exception as exc:
            logger.error(f"Error listing queue: {exc}", exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Error fetching queue.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )

    async def _handle_control(self, message: Message, state: FSMContext | None = None) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        if state:
            await state.clear()
            
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        mode = (self._runtime_get("ops.mode") or "review").strip().lower()
        bot_state = (self._runtime_get("ops.bot_enabled") or "on").strip().lower()
        deployer = (self._runtime_get("ops.deployer_mode") or "clanker").strip().lower()
        auto_threshold = self._runtime_get("ops.auto_threshold") or "90"

        mode_view = "🟩 AUTO" if mode == "auto" else "🟦 REVIEW"
        bot_view = "🟢 ON" if bot_state in {"on", "true", "1", "yes"} else "🔴 OFF"
        deployer_view = deployer.upper()
        if deployer == "both":
            deployer_view = "BOTH (planned)"
        if deployer == "bankr":
            deployer_view = "BANKR (planned)"

        threshold_note = f" (auto-deploys at ≥ {auto_threshold}/100)" if mode == "auto" else ""

        await message.answer(
            _fmt_dashboard_header("Master Dashboard", "⚙️") +
            f"• <b>Mode:</b> {mode_view}{threshold_note}\n"
            f"• <b>Bot:</b> {bot_view}\n"
            f"• <b>Deployer:</b> {_fmt_text(deployer_view)}\n\n"
            "Setters:\n"
            "• <code>/setmode review</code> or <code>/setmode auto</code>\n"
            "• <code>/setthreshold &lt;50-100&gt;</code> — auto-deploy score floor\n"
            "• <code>/setbot on</code> or <code>/setbot off</code>\n"
            "• <code>/setdeployer clanker|bankr|both</code>\n"
            "• <code>/panic</code> — 🚨 Emergency stop",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard(),
        )

    async def _handle_setmode(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            # Interactive Helper
            await self._handle_nav_tools_mode(CallbackQuery(id="0", from_user=message.from_user, chat_instance="0", message=message))
            return
        mode = parts[1].strip().lower()
        if mode not in {"review", "auto"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid mode. Use review or auto.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("ops.mode", mode):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Success", "✅") +
             f"Mode set to <b>{_fmt_text(mode)}</b>.", 
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_setthreshold(self, message: Message) -> None:
        """Set the minimum score required for Auto-Deploy in Auto mode."""
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            current = self._runtime_get("ops.auto_threshold") or "90"
            await message.answer(
                 _fmt_dashboard_header("Auto Threshold", "🎯") +
                 f"Current auto-deploy threshold: <b>{_fmt_inline_code(current)}</b>/100\n\n"
                 "Usage: <code>/setthreshold &lt;50-100&gt;</code>\n"
                 "• Candidates scoring <b>≥ threshold</b> are auto-deployed in Auto mode.\n"
                 "• Lower = more aggressive. Higher = more selective.",
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        try:
            threshold = int(parts[1].strip())
            if not (50 <= threshold <= 100):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid threshold. Must be an integer between <b>50</b> and <b>100</b>.",
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("ops.auto_threshold", str(threshold)):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving threshold.",
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Threshold Updated", "🎯") +
             f"Auto-deploy threshold set to <b>{threshold}/100</b>.\n"
             f"Signals scoring ≥ {threshold} will be deployed automatically when in AUTO mode.",
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_panic(self, message: Message) -> None:
        """EMERGENCY: Immediately force Review mode, halting all autonomous deployments."""
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        # Force mode to review immediately
        saved = self._runtime_set("ops.mode", "review")
        if not saved:
            await message.answer(
                 _fmt_dashboard_header("PANIC FAILED", "🚨") +
                 "⚠️ Could not write to database. <b>Manual intervention required.</b>",
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("PANIC MODE ACTIVATED", "🚨") +
             "🔴 <b>System is now in REVIEW mode.</b>\n\n"
             "All autonomous deployments have been halted.\n"
             "Every incoming signal will require <b>manual approval</b>.\n\n"
             "<i>Run /setmode auto to resume autonomous operation.</i>",
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_setbot(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /setbot &lt;on|off&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        value = parts[1].strip().lower()
        if value not in {"on", "off"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid value. Use on or off.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("ops.bot_enabled", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving bot state.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Success", "✅") +
             f"Bot notifications set to <b>{_fmt_text(value)}</b>.", 
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_setdeployer(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /setdeployer &lt;clanker|bankr|both&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        value = parts[1].strip().lower()
        if value not in {"clanker", "bankr", "both"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid deployer mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("ops.deployer_mode", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving deployer mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        note = ""
        if value in {"bankr", "both"}:
            note = "\n⚠️ Bankr execution is not implemented yet; runtime will fallback to clanker."
        await message.answer(
            _fmt_dashboard_header("Success", "✅") +
            f"Deployer mode set to <b>{_fmt_text(value)}</b>.{note}",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_candidate(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer(
                 _fmt_dashboard_header("Notice", "ℹ️") +
                 "Database not available.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /candidate &lt;candidate_id&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        candidate_id = parts[1].strip()
        try:
            detail_message = await self._render_candidate_detail(candidate_id)
            await message.answer(
                detail_message,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=_build_dashboard_keyboard()
            )
        except Exception as exc:
            logger.error(f"Error fetching candidate detail: {exc}", exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Error fetching candidate detail.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )

    async def _handle_deploys(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer(
                 _fmt_dashboard_header("Notice", "ℹ️") +
                 "Database not available.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        try:
            rows = self._db.list_recent_deployments(limit=10)
            msg = build_deploys_message(rows)
            await message.answer(
                _fmt_dashboard_header("Recent History", "📂") + msg,
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard(),
            )
        except Exception as exc:
            logger.error(f"Error fetching deployments: {exc}", exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Error fetching deployments.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )

    async def _handle_cancel(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await message.answer(
                 _fmt_dashboard_header("Notice", "ℹ️") +
                 "Database not available.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /cancel &lt;candidate_id&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        candidate_id = parts[1].strip()
        review_id = f"review-{candidate_id}"
        try:
            ok = self._db.reject_review_item(review_id, "operator_cancel")
            if ok:
                await message.answer(
                    _fmt_dashboard_header("Action Cancelled", "🚫") +
                    f"Review <code>{candidate_id}</code> cancelled.",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard(),
                )
            else:
                await message.answer(
                    _fmt_dashboard_header("Notice", "⚠️") +
                    f"Could not cancel <code>{candidate_id}</code> — not found or already processed.",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
        except Exception as exc:
            logger.error(f"Error cancelling review: {exc}", exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Error cancelling review.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )

    async def _handle_claimfees(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("claim", thread_id)
        if not self.on_claim_fees:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Claim fees handler is not configured.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            # Interactive Helper
            await self._handle_nav_tools_claim(CallbackQuery(id="0", from_user=message.from_user, chat_instance="0", message=message))
            return
        token_address = parts[1].strip()
        try:
            result = await self.on_claim_fees(token_address)
            if result.status == "claim_success":
                tx_line = f"\n• <b>TX:</b> {_fmt_inline_code(result.tx_hash)}" if result.tx_hash else ""
                claim_msg = (
                    _fmt_dashboard_header("Claim Result", "💸") +
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> success\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}{tx_line}"
                )
                await message.answer(claim_msg, parse_mode="HTML", reply_markup=_build_dashboard_keyboard())
            else:
                claim_msg = (
                    _fmt_dashboard_header("Claim Result", "💸") +
                    "<b>Status</b>\n"
                    "• <b>Outcome:</b> failed\n"
                    f"• <b>Token:</b> {_fmt_inline_code(token_address)}\n"
                    f"• <b>Error:</b> {_fmt_text(result.error_code or 'unknown')}\n"
                    f"• <b>Message:</b> {_fmt_text(result.error_message or 'unknown')}"
                )
                await message.answer(claim_msg, parse_mode="HTML", reply_markup=_build_dashboard_keyboard())
        except Exception as exc:
            logger.error("Error in claim fees handler: %s", exc, exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Claim fees execution failed.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )

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
            _fmt_dashboard_header("Wallet Runtime", "👛") +
            f"• <b>Signer/Deployer:</b> {_fmt_inline_code(signer_display)}\n"
            f"• <b>Token Admin:</b> {_fmt_inline_code(admin_display)}\n"
            f"• <b>Reward Recipient:</b> {_fmt_inline_code(reward_display)}\n\n"
            "Use /setsigner, /setadmin, /setreward to update.\n"
            "Use value <code>default</code> to clear override.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard(),
        )

    async def _handle_setsigner(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /setsigner &lt;address|private_key|default&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.deployer_signer"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting signer override.", 
                     parse_mode="HTML",
                     reply_markup=_build_dashboard_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Signer Status", "⚙️") +
                 "Signer override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not (_is_evm_address(value) or _is_private_key(value)):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid signer. Use EVM address or 0x private key.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("wallet.deployer_signer", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving signer override.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
            _fmt_dashboard_header("Signer Status", "⚙️") +
            f"Signer override updated: {_fmt_inline_code(_mask_sensitive_wallet(value))}",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_setadmin(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /setadmin &lt;address|default&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.token_admin"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting token admin override.", 
                     parse_mode="HTML",
                     reply_markup=_build_dashboard_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Admin Status", "⚙️") +
                 "Token admin override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not _is_evm_address(value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid token admin address.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("wallet.token_admin", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving token admin override.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Admin Settings", "⚙️") +
             f"✅ Token admin updated: {_fmt_inline_code(value)}", 
             parse_mode="HTML", 
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_setreward(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /setreward &lt;address|default&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.fee_recipient"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting reward recipient override.", 
                     parse_mode="HTML",
                     reply_markup=_build_dashboard_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Reward Status", "⚙️") +
                 "Reward recipient override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not _is_evm_address(value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid reward recipient address.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        if not self._runtime_set("wallet.fee_recipient", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving reward recipient override.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Reward Status", "⚙️") +
             f"Reward recipient updated: {_fmt_inline_code(value)}", 
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )

    async def _handle_manualdeploy(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            _fmt_dashboard_header("Manual Deploy Guide", "🧪") +
            "<b>Direct Deploy:</b>\n"
            "<code>/deploynow clanker \"Token Name\" SYMBOL auto optional description</code>\n\n"
            "<b>Deploy Existing Candidate:</b>\n"
            "<code>/deployca clanker &lt;candidate_id&gt;</code>\n\n"
            "Notes:\n"
            "• Current executable platform: <b>clanker</b>\n"
            "• Name with spaces must use quotes",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard(),
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
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Manual deploy handler is not configured.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return

        args = _parse_command_args(message.text or "")
        if len(args) < 4:
            await message.answer(
                _fmt_dashboard_header("Usage", "❓") +
                "Usage: /deploynow &lt;platform&gt; &lt;name&gt; &lt;symbol&gt; &lt;image_or_cid|auto&gt; [description]",
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard()
            )
            return
        platform, token_name, token_symbol, image_ref = args[0], args[1], args[2], args[3]
        description = " ".join(args[4:]).strip() if len(args) > 4 else ""

        if platform.strip().lower() not in {"clanker", "bankr", "both"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid platform. Use clanker|bankr|both.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return

        if len(token_name.strip()) < 2 or len(token_symbol.strip()) < 2:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Token name/symbol too short.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return

        await message.answer(
             _fmt_dashboard_header("Processing", "⏳") +
             "Manual deploy started…", 
             parse_mode="HTML",
             reply_markup=_build_dashboard_keyboard()
        )
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
                    _fmt_dashboard_header("Success", "✅") +
                    "<b>Manual Deploy Success</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
            else:
                await message.answer(
                    _fmt_dashboard_header("Failure", "❌") +
                    "<b>Manual Deploy Failed</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
        except Exception as exc:
            logger.error("Error in deploynow handler: %s", exc, exc_info=True)
            await message.answer(
                _fmt_dashboard_header("Failure", "⚠️") +
                "Manual deploy rejected.\n"
                f"Reason: {_fmt_text(str(exc))}",
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard()
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
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Manual candidate deploy handler is not configured.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return

        args = _parse_command_args(message.text or "")
        if len(args) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /deployca &lt;platform&gt; &lt;candidate_id&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return
        platform, candidate_id = args[0], args[1]
        if platform.strip().lower() not in {"clanker", "bankr", "both"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid platform. Use clanker|bankr|both.", 
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
            )
            return

        await message.answer(
            _fmt_dashboard_header("Processing", "⏳") +
            f"Deploying candidate {_fmt_inline_code(candidate_id)}…",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
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
                    _fmt_dashboard_header("Success", "✅") +
                    "<b>Deploy Candidate Success</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
            else:
                await message.answer(
                    _fmt_dashboard_header("Failure", "❌") +
                    "<b>Deploy Candidate Failed</b>\n\n"
                    f"• <b>Candidate:</b> {_fmt_inline_code(candidate_id)}",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
        except Exception as exc:
            logger.error("Error in deployca handler: %s", exc, exc_info=True)
            await message.answer(
                _fmt_dashboard_header("Failure", "⚠️") +
                "Deploy candidate rejected.\n"
                f"Reason: {_fmt_text(str(exc))}",
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard()
            )

    # ── callback handlers ─────────────────────────────────────────────────────

    async def _handle_approve(self, callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            return
        if not self._is_authorized_chat(callback.message.chat.id):
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
                # Update current message to processing state immediately
                if callback.message:
                    await callback.message.edit_text(
                        _fmt_dashboard_header("Approving", "⌛") +
                        f"Candidate {_fmt_inline_code(candidate_id)} approved.\n"
                        "Check <b>cnc-deploy</b> for logs.",
                        parse_mode="HTML",
                        reply_markup=None, # Remove all buttons
                    )
                
                await self.on_approve(candidate_id)
                await callback.answer("Approved for Deployment")
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
        if not callback.data or not callback.message:
            return
        if not self._is_authorized_chat(callback.message.chat.id):
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
                # Update current message to rejected state immediately
                if callback.message:
                    await callback.message.edit_text(
                        f"❌ <b>Rejected</b>\n\n"
                        f"Candidate {_fmt_inline_code(candidate_id)} has been rejected.",
                        parse_mode="HTML",
                        reply_markup=None, # Remove all buttons
                    )
                
                await self.on_reject(candidate_id)
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

    def _build_review_keyboard(self, candidate_id: str, mode: str = "summary") -> Any:
        return build_review_keyboard(
            candidate_id,
            encode_candidate_id=self._encode_callback_candidate_id,
            mode=mode,
        )

    async def _handle_detail(self, callback: CallbackQuery) -> None:
        if not callback.data or not callback.message:
            return
        if not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
        thread_id = getattr(callback.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("review", thread_id)
        encoded_candidate_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_candidate_id)
        try:
            text = await self._render_candidate_detail(candidate_id)
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=self._build_review_keyboard(candidate_id, mode="detail"),
                disable_web_page_preview=True,
            )
            await callback.answer("Detail View")
        except Exception as exc:
            logger.error("Error handling detail callback: %s", exc, exc_info=True)
            await callback.answer("Detail failed", show_alert=True)

    async def _handle_nav_status(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Status."""
        if not callback.message:
            return
        if not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return

        await self._handle_status(callback.message, state)
        await callback.answer()

    async def _handle_nav_queue(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Queue."""
        if not callback.message:
            return
        if not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
            
        await state.clear()
        await self._handle_queue(callback.message)
        await callback.answer()

    async def _handle_nav_deploys(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Recent Deploys."""
        if not callback.message:
             return
        if not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
            
        await state.clear()
        await self._handle_deploys(callback.message)
        await callback.answer()

    async def _handle_nav_control(self, callback: CallbackQuery) -> None:
        """Dashboard Navigation: Quick jump to Dashboard."""
        if not callback.message:
             return
        await self._handle_control(callback.message)
        await callback.answer()

    async def _handle_nav_tools(self, callback: CallbackQuery) -> None:
        """Navigation: Show master command hub."""
        if not callback.message:
             return
        await callback.message.edit_text(
            _fmt_dashboard_header("System Tools", "🛠") +
            "Direct access to all bot operations and settings:",
            parse_mode="HTML",
            reply_markup=_build_tools_keyboard()
        )
        await callback.answer()

    async def _handle_nav_tools_mode(self, callback: CallbackQuery) -> None:
        """Menu: Mode Toggle."""
        if not callback.message: return
        await callback.message.edit_text(
            _fmt_dashboard_header("Select Operating Mode", "🔄") +
            "• <b>Review:</b> Manual approval required for all signals.\n"
            "• <b>Auto:</b> High-alpha signals are deployed instantly.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="📑 Review Mode", callback_data="exec_mode:review"),
                    InlineKeyboardButton(text="⚡ Auto Mode", callback_data="exec_mode:auto"),
                ],
                [InlineKeyboardButton(text="↩️ Tools", callback_data="nav_tools")]
            ])
        )
        await callback.answer()

    async def _handle_nav_tools_bot(self, callback: CallbackQuery) -> None:
        """Menu: Bot Toggle."""
        if not callback.message: return
        await callback.message.edit_text(
            _fmt_dashboard_header("System State", "🤖") +
            "Enable or disable all automated bot interactions:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🟢 Enable Bot", callback_data="exec_bot:on"),
                    InlineKeyboardButton(text="🔴 Disable Bot", callback_data="exec_bot:off"),
                ],
                [InlineKeyboardButton(text="↩️ Tools", callback_data="nav_tools")]
            ])
        )
        await callback.answer()

    async def _handle_nav_tools_plat(self, callback: CallbackQuery) -> None:
        """Menu: Platform Toggle."""
        if not callback.message: return
        await callback.message.edit_text(
            _fmt_dashboard_header("Select Deployment Platform", "🏗") +
            "Choose where to execute token launches:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🟦 Clanker", callback_data="exec_plat:clanker"),
                    InlineKeyboardButton(text="🟧 Bankr", callback_data="exec_plat:bankr"),
                ],
                [
                    InlineKeyboardButton(text="🟩 Both", callback_data="exec_plat:both"),
                ],
                [InlineKeyboardButton(text="↩️ Tools", callback_data="nav_tools")]
            ])
        )
        await callback.answer()

    async def _handle_nav_tools_pair(self, callback: CallbackQuery) -> None:
        """Menu: Pair Current Topic."""
        if not callback.message: return
        await callback.message.edit_text(
            _fmt_dashboard_header("Bind Topic", "📍") +
            "Select the functional category for this thread:",
            parse_mode="HTML",
            reply_markup=_build_category_keyboard("exec_pair")
        )
        await callback.answer()

    async def _handle_nav_tools_claim(self, callback: CallbackQuery) -> None:
        """Menu: Manual Claim Fees with 8-day rolling window."""
        if not callback.message: return
        if not self._db:
             await callback.answer("Database not available", show_alert=True)
             return
             
        try:
             # Look for recent successful deployments in the last 8 days
             eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
             # We assume list_recent_deployments exists or we use a filter
             rows = self._db.list_recent_deployments(limit=20)
             success_rows = [r for r in rows if r["status"] == "deploy_success" and r["contract_address"] and r["observed_at"] >= eight_days_ago]
             
             if not success_rows:
                  await callback.message.edit_text(
                       _fmt_dashboard_header("Fee Claim", "💸") + 
                       "No successful deployments found in the last 8 days.\n"
                       "<i>Tokens must be deployed via Clank and Claw to appear here.</i>",
                       parse_mode="HTML",
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="↩️ Tools Hub", callback_data="nav_tools")]
                       ])
                  )
                  await callback.answer("No tokens found")
                  return
                  
             keyboard = []
             for r in success_rows:
                  label = f"💰 {r['symbol']} ({r['contract_address'][:8]}…)"
                  keyboard.append([InlineKeyboardButton(text=label, callback_data=f"exec_claim:{r['contract_address']}")])
             keyboard.append([InlineKeyboardButton(text="↩️ Back", callback_data="nav_tools")])
             
             await callback.message.edit_text(
                  _fmt_dashboard_header("Select Token", "💸") + 
                  "Choose a recently deployed token to claim accumulated fees:",
                  parse_mode="HTML",
                  reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
             )
             await callback.answer()
        except Exception as exc:
             logger.error(f"Error in claim tool: {exc}", exc_info=True)
             await callback.answer("Claim menu failed", show_alert=True)

    async def _handle_nav_tools_wallets(self, callback: CallbackQuery) -> None:
        """Action: Show Wallet Status."""
        if not callback.message: return
        await self._handle_wallets(callback.message)
        await callback.answer()

    async def _handle_nav_tools_auto(self, callback: CallbackQuery) -> None:
        """Action: Run Autothread."""
        if not callback.message: return
        await self._handle_autothread(callback.message)
        await callback.answer()

    async def _handle_exec_pair(self, callback: CallbackQuery) -> None:
        """Action: Finalize pairing via button."""
        if not callback.data or not callback.message: return
        category = callback.data.split(":")[1]
        thread_id = getattr(callback.message, "message_thread_id", None)
        
        # We reuse the logic from _handle_pair but without the arg parsing
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread(category, thread_id)
        
        await callback.message.edit_text(
            _fmt_dashboard_header("Success", "✅") +
            f"Thread bound to <b>{category.upper()}</b>.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )
        await callback.answer(f"Bound to {category}")

    async def _handle_exec_setmode(self, callback: CallbackQuery) -> None:
        """Action: Finalize mode via button."""
        if not callback.data or not callback.message: return
        value = callback.data.split(":")[1]
        self._runtime_set("ops.mode", value)
        await callback.message.edit_text(
            _fmt_dashboard_header("Mode Updated", "✅") +
            f"Operating mode set to <b>{value.upper()}</b>.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )
        await callback.answer(f"Mode: {value}")

    async def _handle_exec_setbot(self, callback: CallbackQuery) -> None:
        """Action: Finalize bot toggle via button."""
        if not callback.data or not callback.message: return
        value = callback.data.split(":")[1]
        self._runtime_set("ops.bot_enabled", value)
        status = "ENABLED" if value == "on" else "DISABLED"
        await callback.message.edit_text(
            _fmt_dashboard_header("Bot Status", "✅") +
            f"Automated interactions are now <b>{status}</b>.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )
        await callback.answer(f"Bot {status}")

    async def _handle_exec_setplat(self, callback: CallbackQuery) -> None:
        """Action: Finalize platform via button."""
        if not callback.data or not callback.message: return
        value = callback.data.split(":")[1]
        self._runtime_set("ops.deployer_mode", value)
        await callback.message.edit_text(
            _fmt_dashboard_header("Platform Updated", "✅") +
            f"Execution target set to <b>{value.upper()}</b>.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )
        await callback.answer(f"Platform: {value}")

    async def _handle_exec_claim(self, callback: CallbackQuery) -> None:
        """Action: Finalize claim via button."""
        if not callback.data or not callback.message: return
        address = callback.data.split(":")[1]
        if not self.on_claim_fees:
             await callback.answer("Claim handler missing", show_alert=True)
             return
             
        await callback.message.edit_text(
            _fmt_dashboard_header("Claiming", "⌛") +
            f"Starting claim sequence for <code>{address}</code>...",
            parse_mode="HTML",
            reply_markup=None
        )
        
        try:
            result = await self.on_claim_fees(address)
            if result.status == "claim_success":
                 await callback.message.edit_text(
                     _fmt_dashboard_header("Claim Success", "✅") +
                     f"Successfully claimed fees for <code>{address}</code>.\n\n"
                     f"• <b>TX:</b> <code>{result.tx_hash}</code>",
                     parse_mode="HTML",
                     reply_markup=_build_dashboard_keyboard()
                 )
            else:
                 await callback.message.edit_text(
                     _fmt_dashboard_header("Claim Failed", "❌") +
                     f"Failed claiming fees for <code>{address}</code>.\n"
                     f"Reason: <i>{result.error_message}</i>",
                     parse_mode="HTML",
                     reply_markup=_build_dashboard_keyboard()
                 )
        except Exception as exc:
             await callback.message.edit_text(
                 _fmt_dashboard_header("Error", "⚠️") + f"Internal error during claim: {exc}",
                 parse_mode="HTML",
                 reply_markup=_build_dashboard_keyboard()
             )
        await callback.answer()

    async def _handle_nav_help(self, callback: CallbackQuery) -> None:
        """Dashboard Navigation: Quick jump to Help."""
        if not callback.message:
             return
        await self._handle_help(callback.message)
        await callback.answer()

    # ── Wizard Handlers ───────────────────────────────────────────────────────

    async def _handle_nav_wizard(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Start Interactive Manual Deploy Wizard."""
        if not callback.message:
             return
        if not self._is_authorized_chat(callback.message.chat.id):
            await callback.answer("Unauthorized", show_alert=True)
            return
            
        await state.clear()
        await state.set_state(ManualDeployStates.platform)
        
        await callback.message.edit_text(
            _fmt_dashboard_header("Platform Choice", "🧪") +
            "Please select the deployment platform:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="🟦 CLANKER", callback_data="wiz_plat:clanker"),
                        InlineKeyboardButton(text="🟧 BANKR", callback_data="wiz_plat:bankr"),
                    ],
                    [
                        InlineKeyboardButton(text="🟩 BOTH", callback_data="wiz_plat:both"),
                    ],
                    [
                        InlineKeyboardButton(text="❌ Cancel Setup", callback_data="wiz_cancel"),
                    ]
                ]
            )
        )
        await callback.answer()

    async def _handle_wizard_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Start Wizard pre-filled from an existing candidate."""
        if not callback.data or not callback.message:
            return
        encoded_id = callback.data.split(":", 1)[1]
        candidate_id = self._decode_callback_candidate_id(encoded_id)
        
        candidate = self._db.get_candidate(candidate_id)
        if not candidate:
             await callback.answer("Candidate not found", show_alert=True)
             return
             
        metadata = {}
        try:
            metadata = json.loads(candidate["metadata_json"] or "{}")
        except: pass
        
        await state.clear()
        # Pre-fill data
        await state.update_data(
            platform="clanker", # Default
            name=candidate["suggested_name"] or metadata.get("suggested_name") or "",
            symbol=candidate["suggested_symbol"] or metadata.get("suggested_symbol") or "",
            image=metadata.get("image_url") or metadata.get("ipfs_image_uri") or "auto",
            description=metadata.get("ai_description") or "",
            candidate_id=candidate_id
        )
        
        # Start at platform step but with pre-filled context
        await state.set_state(ManualDeployStates.platform)
        await self._handle_nav_wizard(callback, state)
        await callback.answer("Editing Candidate...")

    async def _handle_wizard_back(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Navigate back to previous step in the wizard."""
        if not callback.message:
             return
        current_state = await state.get_state()
        
        if current_state == ManualDeployStates.name:
            await state.set_state(ManualDeployStates.platform)
            await self._handle_nav_wizard(callback, state)
        elif current_state == ManualDeployStates.symbol:
            await state.set_state(ManualDeployStates.name)
            await self._show_wizard_name_step(callback, state)
        elif current_state == ManualDeployStates.image:
            await state.set_state(ManualDeployStates.symbol)
            data = await state.get_data()
            await callback.message.edit_text(
                _fmt_dashboard_header("Token Identity", "🧪") +
                f"• <b>Platform:</b> {data['platform'].upper()}\n"
                f"• <b>Name:</b> {html.escape(data['name'])}\n\n"
                "Please type the <b>Token Symbol</b>:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
                ])
            )
        elif current_state == ManualDeployStates.description:
            await state.set_state(ManualDeployStates.image)
            data = await state.get_data()
            await callback.message.edit_text(
                _fmt_dashboard_header("Visuals", "🧪") +
                f"• <b>Platform:</b> {data['platform'].upper()}\n"
                f"• <b>Name:</b> {html.escape(data['name'])}\n"
                f"• <b>Symbol:</b> {data['symbol']}\n\n"
                "Please send an <b>Image URL</b> or <b>Photo</b>:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
                ])
            )
        elif current_state == ManualDeployStates.confirm:
            await state.set_state(ManualDeployStates.description)
            await self._show_wizard_desc_step(callback, state)
        await callback.answer()

    async def _handle_wizard_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """AI Suggestion for Name or Symbol."""
        if not callback.message:
             return
        data = await state.get_data()
        
        # Determine theme from existing name or a default
        theme = data.get("name") or "trending base meme"
        await callback.answer("🪄 AI Thinking...", show_alert=False)
        
        suggestions = await suggest_token_metadata(theme)
        if not suggestions:
             await callback.answer("AI failed to suggest. Try again.", show_alert=True)
             return
             
        keyboard = []
        for s in suggestions:
            # We use a special prefix to catch the choice
            label = f"{s['name']} ({s['symbol']})"
            keyboard.append([InlineKeyboardButton(text=label, callback_data=f"wiz_apply_suggest:{s['name']}:{s['symbol']}")])
        
        keyboard.append([InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")])
        keyboard.append([InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")])
        
        await callback.message.edit_text(
            _fmt_dashboard_header("AI Suggestions", "🪄") +
            "Based on the current context, here are some ideas:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

    async def _handle_wizard_apply_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Apply the AI suggestion and move to next step."""
        if not callback.data or not callback.message:
            return
        parts = callback.data.split(":", 2)
        if len(parts) < 3:
             return
        name, symbol = parts[1], parts[2]
        await state.update_data(name=name, symbol=symbol)
        
        # Advance to Image step directly as both are now filled
        await state.set_state(ManualDeployStates.image)
        data = await state.get_data()
        await callback.message.edit_text(
            _fmt_dashboard_header("Visuals", "🧪") +
            f"• <b>Name:</b> {html.escape(name)}\n"
            f"• <b>Symbol:</b> {symbol}\n\n"
            "Please send an <b>Image URL</b> or <b>Photo</b>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ])
        )
        await callback.answer(f"Applied: {symbol}")

    async def _handle_wizard_platform(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 1: Platform Selection."""
        if not callback.message or not callback.data:
            return
        platform = callback.data.split(":")[1]
        await state.update_data(platform=platform)
        
        data = await state.get_data()
        # Optimization: If we already have Name/Symbol from Edit flow, skip to summary?
        # No, let's just go to Name step but show the pre-filled value if it exists.
        
        await state.set_state(ManualDeployStates.name)
        existing_name = data.get("name", "")
        
        msg_text = (
            _fmt_dashboard_header("Token Identity", "🧪") +
            f"• <b>Platform:</b> {platform.upper()}\n\n"
            "Please type the <b>Token Name</b>:"
        )
        if existing_name:
            msg_text += f"\n(Current: <code>{html.escape(existing_name)}</code>)"

        await callback.message.edit_text(
            msg_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🪄 AI Suggest Name", callback_data="wiz_suggest")],
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
                ]
            )
        )
        await callback.answer()

    async def _handle_wizard_name(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 2: Capture Token Name."""
        name = (message.text or "").strip()
        if not name:
             await message.answer("⚠️ No text found. Please type the <b>Token Name</b>:", parse_mode="HTML")
             return
        if len(name) < 2:
            await message.answer("⚠️ Name too short. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(name=name)
        await state.set_state(ManualDeployStates.symbol)
        await self._show_wizard_symbol_step(message, state)

    async def _show_wizard_name_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Name step UI."""
        data = await state.get_data()
        existing_name = data.get("name", "")
        platform = data.get("platform", "clanker")
        
        msg_text = (
            _fmt_dashboard_header("Token Identity", "🧪") +
            f"• <b>Platform:</b> {platform.upper()}\n\n"
            "Please type the <b>Token Name</b>:"
        )
        if existing_name:
            msg_text += f"\n(Current: <code>{html.escape(existing_name)}</code>)"

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Suggest Name", callback_data="wiz_suggest")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ]
        )
        
        if isinstance(message, CallbackQuery):
            await message.message.edit_text(msg_text, parse_mode="HTML", reply_markup=markup)
        else:
            await message.answer(msg_text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_symbol_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Symbol step UI."""
        data = await state.get_data()
        name = data.get("name", "n/a")
        existing_symbol = data.get("symbol", "")
        
        msg_text = (
            _fmt_dashboard_header("Token Identity", "🧪") +
            f"• <b>Platform:</b> {data['platform'].upper()}\n"
            f"• <b>Name:</b> {html.escape(name)}\n\n"
            "Please type the <b>Token Symbol</b>:"
        )
        if existing_symbol:
            msg_text += f"\n(Current: <code>{existing_symbol}</code>)"

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Suggest Symbol", callback_data="wiz_suggest")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ]
        )
        
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(msg_text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(msg_text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_image_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Image step UI."""
        data = await state.get_data()
        msg_text = (
            _fmt_dashboard_header("Visuals", "🧪") +
            f"• <b>Platform:</b> {data['platform'].upper()}\n"
            f"• <b>Name:</b> {html.escape(data['name'])}\n"
            f"• <b>Symbol:</b> {data['symbol']}\n\n"
            "Please send an <b>Image URL</b> or upload a <b>Photo</b>:"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ]
        )
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(msg_text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(msg_text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_desc_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Description step UI."""
        data = await state.get_data()
        msg_text = (
            _fmt_dashboard_header("Metadata", "🧪") +
            f"• <b>Symbol:</b> {data['symbol']}\n"
            f"• <b>Image:</b> 🪄 {data.get('image', 'n/a')}\n\n"
            "Please type the <b>Token Description</b> (optional):"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Generate Desc", callback_data="wiz_desc_suggest")],
                [InlineKeyboardButton(text="⏭ Skip", callback_data="wiz_desc:skip")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ]
        )
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(msg_text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(msg_text, parse_mode="HTML", reply_markup=markup)

    async def _handle_wizard_symbol(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 3: Capture Token Symbol."""
        symbol = (message.text or "").strip().upper()
        if not symbol:
             await message.answer("⚠️ No text found. Please type the <b>Token Symbol</b>:", parse_mode="HTML")
             return
        if len(symbol) < 2:
            await message.answer("⚠️ Symbol too short. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(symbol=symbol)
        await state.set_state(ManualDeployStates.image)
        await self._show_wizard_image_step(message, state)

    async def _handle_wizard_image(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 4: Capture Token Image (URL or Photo upload)."""
        image_ref = ""
        
        if message.photo:
             # Handle direct photo upload
             await message.answer("⏳ Processing photo upload to IPFS...", parse_mode="HTML")
             try:
                 photo = message.photo[-1] # Largest version
                 from io import BytesIO
                 # We need the bot object which is in self.bot
                 file_info = await self.bot.get_file(photo.file_id)
                 downloaded = await self.bot.download_file(file_info.file_path, BytesIO())
                 
                 # Upload to Pinata via deploy_preparation helper logic if possible?
                 # No, let's just use pinata_client directly if available or a mock
                 if hasattr(self, "_pinata") and self._pinata:
                      ipfs_hash = await self._pinata.upload_file_bytes(
                          f"manual_{photo.file_id}.jpg", 
                          downloaded.getvalue(),
                          "image/jpeg"
                      )
                      image_ref = f"ipfs://{ipfs_hash}"
                 else:
                      await message.answer("⚠️ IPFS uploader not configured. Use URL instead.", parse_mode="HTML")
                      return
             except Exception as exc:
                 logger.error(f"Manual photo upload failed: {exc}")
                 await message.answer(f"❌ Upload failed: {exc}", parse_mode="HTML")
                 return
        else:
             image_ref = (message.text or "").strip()

        if not image_ref:
            await message.answer("⚠️ Please provide an image. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(image=image_ref)
        await state.set_state(ManualDeployStates.description)
        await self._show_wizard_desc_step(message, state)

    async def _handle_wizard_image_auto(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 4: Set Image to 'auto'."""
        if not callback.message:
             return
        await state.update_data(image="auto")
        await state.set_state(ManualDeployStates.description)
        await self._show_wizard_desc_step(callback, state)
        await callback.answer("Image set to AUTO")

    async def _handle_wizard_desc_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """AI Suggestion for Description."""
        if not callback.message:
             return
        data = await state.get_data()
        await callback.answer("🪄 AI Writing...", show_alert=False)
        
        desc = await suggest_token_description(
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "TKN"),
            theme=data.get("name", "")
        )
        if not desc:
             await callback.answer("AI failed to write. Try again.", show_alert=True)
             return
             
        await state.update_data(description=desc)
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(callback.message, state)
        await callback.answer("Description Generated!")

    async def _handle_wizard_description_skip(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 5 Helper: Skip Description."""
        if not callback.message:
            return
        await state.update_data(description="")
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(callback.message, state)
        await callback.answer()

    async def _handle_wizard_description(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 5: Capture Description."""
        description = (message.text or "").strip()
        await state.update_data(description=description)
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(message, state)

    async def _show_wizard_preview(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Final Wizard Preview Card."""
        data = await state.get_data()
        await message.answer(
            _fmt_dashboard_header("Preview Deployment", "🚀") +
            f"• <b>Platform:</b> {data['platform'].upper()}\n"
            f"• <b>Name:</b> {_fmt_inline_code(data['name'])}\n"
            f"• <b>Symbol:</b> {_fmt_inline_code(data['symbol'])}\n"
            f"• <b>Image:</b> <code>{html.escape(data['image'][:30])}...</code>\n"
            f"• <b>Desc:</b> {_fmt_text(data['description'])}\n\n"
            "Confirm deployment strategy and launch?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 Launch Deployment", callback_data="wiz_confirm")],
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel Setup", callback_data="wiz_cancel")]
                ]
            )
        )

    async def _handle_wizard_confirm(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Final Wizard Trigger: Execute Deployment."""
        if not callback.message:
             return
        data = await state.get_data()
        await state.clear()
        
        await callback.message.edit_text(
            _fmt_dashboard_header("Deployment Started", "⌛") +
            f"Manual deployment for <b>{data['name']}</b> has been triggered.\n"
            "Check <b>cnc-deploy</b> for logs.",
            parse_mode="HTML",
        )
        
        if self.on_manual_deploy:
            try:
                await self.on_manual_deploy(
                    data["platform"],
                    data["name"],
                    data["symbol"],
                    data["image"],
                    data["description"],
                    {
                        "chat_id": callback.message.chat.id,
                        "user_id": getattr(callback.from_user, "id", None),
                    }
                )
            except Exception as exc:
                logger.error(f"Wizard deploy error: {exc}", exc_info=True)
                await callback.message.answer(
                    _fmt_dashboard_header("Deployment Failed", "❌") + 
                    f"Reason: {_fmt_text(str(exc))}",
                    parse_mode="HTML",
                    reply_markup=_build_dashboard_keyboard()
                )
        await callback.answer()

    async def _handle_wizard_cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Cancel Handler: Reset FSM and show Dashboard."""
        if not callback.message:
            return
        await state.clear()
        await callback.message.edit_text(
            _fmt_dashboard_header("Setup Cancelled", "🚫") +
            "Manual deployment wizard was dismissed.",
            parse_mode="HTML",
            reply_markup=_build_dashboard_keyboard()
        )
        await callback.answer("Cancelled")

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
            
            # Add a "last updated" line at the very end
            timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            updated += f"\n\n<i>Refreshed: {timestamp} UTC</i>"

            await callback.message.edit_text(
                updated,
                parse_mode="HTML",
                reply_markup=self._build_review_keyboard(candidate_id, mode="summary"),
                disable_web_page_preview=True,
            )
            await callback.answer("Refreshed Summary")
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
        try:
            created, failures = await self._ensure_forum_topics_bound()
            if created:
                logger.info("telegram.auto_thread_setup created=%s", ",".join(created))
            if failures:
                logger.warning("telegram.auto_thread_setup failures=%s", " | ".join(failures))
        except Exception as exc:
            logger.warning("telegram.auto_thread_setup failed: %s", exc)
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping Telegram bot")
        await self.bot.session.close()
