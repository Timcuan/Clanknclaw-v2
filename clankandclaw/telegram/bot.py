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

from clankandclaw.utils.llm import enrich_signal_with_llm, suggest_token_metadata, suggest_token_description
from clankandclaw.telegram.formatters import (
    _fmt_text, _fmt_inline_code, _fmt_dashboard_header, _source_label, _network_icon,
    _fmt_num, _is_evm_address, _mask_sensitive_wallet, _parse_command_args,
    _fmt_truncate, _get_explorer_url,
)
from clankandclaw.telegram.ui import (
    _build_dashboard_keyboard, _build_back_home_keyboard, _build_tools_keyboard, _build_category_keyboard,
    build_action_callback_data as ui_build_action_callback_data,
    build_forum_topic_plan as ui_build_forum_topic_plan,
    _SIGNAL_MAP,
)

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher
    from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.filters import Command, StateFilter
    from aiogram.fsm.context import FSMContext
    from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
    from clankandclaw.telegram.wizard import WizardHandler, ManualDeployStates
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
    TelegramBadRequest = Exception  # type: ignore
    FSMContext = Any  # type: ignore
    State = Any  # type: ignore
    StatesGroup = Any  # type: ignore
    WizardHandler = Any  # type: ignore
    ManualDeployStates = Any  # type: ignore

logger = logging.getLogger(__name__)


_THREAD_CATEGORIES = ("review", "deploy", "claim", "ops", "alert")
_MAX_CALLBACK_DATA = 64
_MAX_RAW_TEXT = 300
_MAX_QUEUE_ITEMS = 10
_MAX_ERROR_TEXT = 80
_MAX_REASONS = 6

_DEFAULT_FORUM_TOPIC_TITLES = {
    "review": "🛰 cnc-review",
    "deploy": "🧪 cnc-deploy",
    "claim": "💸 cnc-claim",
    "ops": "🗺 cnc-ops",
    "alert": "⚠️ cnc-alert",
}


def resolve_authorized_chat_id(configured_id: str | None, runtime_id: str | None) -> str | None:
    """Determine the effective chat ID with persistence priority."""
    if runtime_id is not None and str(runtime_id).strip():
        return str(runtime_id).strip()
    if configured_id is not None and str(configured_id).strip():
        return str(configured_id).strip()
    return None


def build_action_callback_data(
    action: str,
    candidate_id: str,
    *,
    encode_candidate_id: Callable[[str], str] | None = None,
) -> str:
    return ui_build_action_callback_data(
        action,
        candidate_id,
        encode_candidate_id=encode_candidate_id,
    )


def build_forum_topic_plan(existing_thread_bindings: dict[str, int] | None = None) -> list[tuple[str, str]]:
    return ui_build_forum_topic_plan(existing_thread_bindings)


def _shorten_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _normalize_thread_id(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _format_reason_label(reason: str) -> str:
    text = str(reason or "").strip().lower()
    for prefix in ("gecko_", "x_", "farcaster_", "base_", "network_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("_", " ").strip()
    return text if text else "signal"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Compat getter for dict-like objects and sqlite3.Row."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        keys = row.keys()
    except Exception:
        keys = ()
    if key in keys:
        try:
            return row[key]
        except Exception:
            return default
    return default


def _parse_detector_health(raw_value: str | None) -> dict[str, str]:
    if not raw_value:
        return {"status": "unknown", "reason": "", "until": ""}
    try:
        parsed = json.loads(raw_value)
        if isinstance(parsed, dict):
            return {
                "status": str(parsed.get("status") or "unknown").strip().lower(),
                "reason": str(parsed.get("reason") or "").strip(),
                "until": str(parsed.get("until") or "").strip(),
            }
    except Exception:
        pass
    return {"status": "unknown", "reason": "", "until": ""}


def build_detector_health_alerts(runtime_get: Callable[[str], str | None]) -> str:
    labels = [
        ("health.x_detector", "X Detector"),
        ("health.farcaster_detector", "Farcaster Detector"),
    ]
    lines: list[str] = []
    for key, label in labels:
        health = _parse_detector_health(runtime_get(key))
        status = health.get("status", "unknown")
        if status in {"ok", "healthy", ""}:
            continue
        reason = health.get("reason") or "unknown"
        until = health.get("until") or ""
        line = f"🟠 <b>{label}:</b> {html.escape(reason)}"
        if until:
            line += f" (until {html.escape(until)})"
        lines.append(line)
    return "\n".join(lines)


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
    del candidate_id
    metadata = metadata or {}

    priority_emoji = "🔥" if review_priority == "priority_review" else "📋"
    priority_label = "High" if review_priority == "priority_review" else "Review"
    source_label = _source_label(source)
    token_name = _fmt_text(metadata.get("token_name"), fallback="Unknown")
    token_symbol = _fmt_text(metadata.get("token_symbol"), fallback="N/A")
    network = _fmt_text(metadata.get("network"), fallback="unknown")
    network_icon = _network_icon(network)

    # Market data
    liquidity_usd = _fmt_num(metadata.get("liquidity_usd"), digits=0, fallback="0")
    volume = metadata.get("volume") or {}
    tx_data = metadata.get("transactions") or {}
    volume_m5 = _fmt_num(volume.get("m5"), digits=0, fallback="0")
    tx_m5_raw = tx_data.get("m5")
    if isinstance(tx_m5_raw, dict):
        tx_display = f"{tx_m5_raw.get('buys', '?')}B / {tx_m5_raw.get('sells', '?')}S"
    else:
        tx_display = _fmt_num(tx_m5_raw, fallback="0")
    fdv_raw = metadata.get("fdv_usd")
    fdv_str = f" · FDV ${_fmt_num(fdv_raw, digits=0)}" if fdv_raw else ""
    age_raw = metadata.get("pool_age_minutes")
    age_str = f" · {int(age_raw)}m old" if (age_raw is not None and age_raw < 999) else ""

    # Signals — use icons from _SIGNAL_MAP, fall back to formatted label
    _SKIP = {"network_base", "base_score"}
    signal_parts: list[str] = []
    for code in reason_codes[:_MAX_REASONS]:
        if code in _SKIP or code.startswith("llm_risk_"):
            continue
        signal_parts.append(_SIGNAL_MAP.get(code) or _format_reason_label(code))
    signals = ", ".join(signal_parts) if signal_parts else "—"
    if len(reason_codes) > _MAX_REASONS:
        signals += f" (+{len(reason_codes) - _MAX_REASONS})"

    lines = [
        f"{network_icon} <b>{token_name}</b> <code>${token_symbol}</code>",
        f"<i>{source_label} · {network.upper()} · {priority_emoji} {priority_label}</i>",
        "",
        f"• <b>Score:</b> {_fmt_num(score)}",
        f"• <b>Market:</b> Liq ${liquidity_usd} · Vol(5m) ${volume_m5} · Tx {tx_display}{fdv_str}{age_str}",
    ]

    # CA with explorer link
    ca = str(metadata.get("token_address") or "")
    if ca and len(ca) > 10:
        ca_url = html.escape(_get_explorer_url(network, "address", ca), quote=True)
        lines.append(f"• <b>CA:</b> <code>{ca}</code> <a href=\"{ca_url}\">↗</a>")

    lines.append(f"• <b>Signals:</b> {_fmt_text(_shorten_text(signals, 140), fallback='—')}")

    # Social links
    social_links: list[str] = []
    for web in metadata.get("websites") or []:
        if isinstance(web, str) and web.startswith("http"):
            social_links.append(f"<a href='{html.escape(web, quote=True)}'>🌐 Web</a>")
    for soc in metadata.get("socials") or []:
        if not isinstance(soc, str):
            continue
        if "twitter.com" in soc or "x.com" in soc:
            social_links.append(f"<a href='{html.escape(soc, quote=True)}'>✖ X</a>")
        elif "t.me" in soc or "telegram.org" in soc:
            social_links.append(f"<a href='{html.escape(soc, quote=True)}'>✈ TG</a>")
    if social_links:
        lines.append("• <b>Links:</b> " + " · ".join(social_links))

    if author_handle:
        lines.append(f"• <b>Author:</b> @{_fmt_text(author_handle, fallback='unknown')}")

    if context_url:
        safe_url = html.escape(context_url, quote=True)
        lines.append(f'• <a href="{safe_url}"><i>Open source</i></a>')

    if raw_text:
        trimmed = _shorten_text(raw_text, 180)
        lines += ["", f"<blockquote>{_fmt_text(trimmed)}</blockquote>"]

    return "\n".join(lines)


def build_queue_message(rows: list[Any]) -> str:
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
    meta_raw = candidate["metadata_json"] if "metadata_json" in candidate.keys() else "{}"
    try:
        meta = json.loads(meta_raw or "{}")
    except Exception:
        meta = {}

    token_name = _fmt_text(meta.get("token_name") or meta.get("suggested_name"), fallback="Unknown")
    token_symbol = _fmt_text(meta.get("token_symbol") or meta.get("suggested_symbol"), fallback="N/A")
    network = _fmt_text(meta.get("network"), fallback="unknown")
    network_icon = _network_icon(network)
    volume = meta.get("volume") or {}
    tx_data = meta.get("transactions") or {}
    volume_m5 = _fmt_num(volume.get("m5"), digits=2, fallback="0.00")
    liquidity = _fmt_num(meta.get("liquidity_usd"), digits=2, fallback="0.00")
    tx_m5 = _fmt_num(tx_data.get("m5"), fallback="0")
    score = decision["score"] if decision else "n/a"
    decision_label = decision["decision"] if decision else "n/a"
    signals = _row_get(decision, "reason_codes", "—") if decision else "—"
    if not signals:
        signals = "—"
    review_status = review_item["status"] if review_item else "n/a"
    deploy_status = deployment["status"] if deployment else "n/a"
    short_id = _fmt_truncate(candidate["id"], 20)

    lines = [
        f"{network_icon} <b>{token_name}</b> <code>${token_symbol}</code>",
        f"<i>{_source_label(candidate['source'])} • {_fmt_text(network).upper()} • {_fmt_text(short_id)}</i>",
        "",
        "<b>Snapshot</b>",
        f"• <b>Score:</b> {_fmt_text(score)} • <b>Decision:</b> {_fmt_text(decision_label)}",
        f"• <b>Market:</b> m5 ${volume_m5} • tx {tx_m5} • liq ${liquidity}",
        f"• <b>Signals:</b> {_fmt_text(_shorten_text(str(signals), 140), fallback='—')}",
        f"• <b>Review:</b> {_fmt_text(review_status)} • <b>Deploy:</b> {_fmt_text(deploy_status)}",
    ]
    if meta.get("author_handle"):
        lines.append(f"• <b>Author:</b> @{_fmt_text(meta.get('author_handle'))}")
    if meta.get("context_url"):
        lines.append(f'• <a href="{html.escape(meta["context_url"], quote=True)}"><i>Open source</i></a>')
    contract_address = _row_get(deployment, "contract_address")
    tx_hash = _row_get(deployment, "tx_hash")
    if contract_address:
        lines.append(f"• <b>CA:</b> <code>{contract_address}</code>")
    if tx_hash:
        lines.append(f"• <b>TX:</b> <code>{tx_hash}</code>")

    raw_text = candidate["raw_text"] or ""
    if raw_text:
        trimmed = raw_text[:_MAX_RAW_TEXT]
        if len(raw_text) > _MAX_RAW_TEXT:
            trimmed += "…"
        lines += ["", f"<blockquote>{_fmt_text(trimmed)}</blockquote>"]

    return "\n".join(lines)


def build_deploys_message(rows: list[Any]) -> str:
    if not rows:
        return "📭 No deployments yet."

    lines = [f"📂 <b>Recent Deployments</b>", f"Total: <b>{len(rows)}</b>", ""]
    for row in rows:
        if row["status"] == "deploy_success":
            contract = row["contract_address"] or ""
            tx = row["tx_hash"] or ""
            # Infer network from candidate_id for explorer links
            cid = str(row["candidate_id"] or "").lower()
            net = "solana" if "solana" in cid else "bsc" if "bsc" in cid else "eth" if "eth" in cid else "base"
            ca_link = ""
            tx_link = ""
            if contract:
                ca_url = html.escape(_get_explorer_url(net, "address", contract), quote=True)
                ca_link = f"{_fmt_inline_code(contract)} <a href=\"{ca_url}\">↗</a>"
            if tx:
                tx_url = html.escape(_get_explorer_url(net, "tx", tx), quote=True)
                tx_link = f"{_fmt_inline_code(tx)} <a href=\"{tx_url}\">↗</a>"
            lines.append(f"✅ {_fmt_inline_code(row['candidate_id'])} | {ca_link} | {tx_link}")
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
    context_url: str | None = None,
    *,
    encode_candidate_id: Callable[[str], str] | None = None,
) -> Any:
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    keyboard = [
        [
            InlineKeyboardButton(
                text="🚀 Deploy",
                callback_data=build_action_callback_data(
                    "approve",
                    candidate_id,
                    encode_candidate_id=encode_candidate_id,
                ),
            ),
            InlineKeyboardButton(
                text="🔎 Detail",
                callback_data=build_action_callback_data(
                    "detail",
                    candidate_id,
                    encode_candidate_id=encode_candidate_id,
                ),
            ),
        ],
    ]
    if context_url:
        keyboard.append([InlineKeyboardButton(text="🔗 Source", url=context_url)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)



#
# Core Bot Manager
#



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
        self.message_thread_id = _normalize_thread_id(message_thread_id)
        self.thread_review_id = _normalize_thread_id(thread_review_id)
        self.thread_deploy_id = _normalize_thread_id(thread_deploy_id)
        self.thread_claim_id = _normalize_thread_id(thread_claim_id)
        self.thread_ops_id = _normalize_thread_id(thread_ops_id)
        self.thread_alert_id = _normalize_thread_id(thread_alert_id)
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
        
        # New specialized handlers
        self.wizard = WizardHandler(self)
        
        self._setup_handlers()

        # Callback handlers (set by worker)
        self.on_approve: Any = None
        self.on_reject: Any = None
        self.on_claim_fees: Any = None
        self.on_manual_deploy: Any = None
        self.on_manual_deploy_candidate: Any = None
        self._binding_cache: set[str] = set()
        self._load_dynamic_thread_bindings()
        runtime_chat_id = self._runtime_get("telegram.chat_id")
        resolved_chat_id = resolve_authorized_chat_id(configured_chat_id, runtime_chat_id)
        if resolved_chat_id:
            self.chat_id = resolved_chat_id

    def _persist_dynamic_thread_binding(self, category: str, thread_id: int) -> None:
        if not self._db:
            return
        
        cache_key = f"{category}:{thread_id}"
        if cache_key in self._binding_cache:
            return
            
        setter = getattr(self._db, "set_runtime_setting", None)
        if not callable(setter):
            return
        try:
            setter(f"telegram.thread.{category}", str(thread_id))
            self._binding_cache.add(cache_key)
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
                self._binding_cache.add(f"{category}:{parsed}")

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
            "review": _normalize_thread_id(self.thread_review_id),
            "deploy": _normalize_thread_id(self.thread_deploy_id),
            "claim": _normalize_thread_id(self.thread_claim_id),
            "ops": _normalize_thread_id(self.thread_ops_id),
            "alert": _normalize_thread_id(self.thread_alert_id),
        }
        if configured.get(category) is not None:
            return

        # NEW: Check if this thread_id is already taken by ANOTHER category
        # First check hardcoded/env IDs
        for cat, cid in configured.items():
            if cid is not None and int(cid) == parsed:
                return

        # Then check existing dynamic bindings
        for cat, cid in self._dynamic_thread_bindings.items():
            if cid == parsed and cat != category:
                # This ID is already assigned to another category (e.g. 'review')
                # so don't allow 'ops' to take it.
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

    def _build_review_keyboard(self, candidate_id: str, context_url: str | None = None) -> Any:
        return build_review_keyboard(
            candidate_id,
            context_url=context_url,
            encode_candidate_id=self._encode_callback_candidate_id,
        )

    def _ui_dashboard_keyboard(self) -> InlineKeyboardMarkup:
        """Expose dashboard keyboard for specialized handlers."""
        return _build_dashboard_keyboard()

    def _resolve_message_thread_id(self, explicit_thread_id: int | None = None) -> int | None:
        explicit = _normalize_thread_id(explicit_thread_id)
        if explicit is not None:
            return explicit
        if _normalize_thread_id(self.message_thread_id) is not None:
            return self.message_thread_id
        if _normalize_thread_id(self._last_operator_thread_id) is not None:
            return self._last_operator_thread_id
        return None

    def _thread_for(self, category: str) -> int | None:
        """Resolve the target thread for a category with strict priority and fallback."""
        if not self.chat_id:
             return None
             
        mapping = {
            "review": _normalize_thread_id(self.thread_review_id),
            "deploy": _normalize_thread_id(self.thread_deploy_id),
            "claim": _normalize_thread_id(self.thread_claim_id),
            "ops": _normalize_thread_id(self.thread_ops_id),
            "alert": _normalize_thread_id(self.thread_alert_id),
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
            resolved_thread_id = _normalize_thread_id(message_thread_id)
            return await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
                message_thread_id=resolved_thread_id,
            )
        except Exception as exc:
            error_text = str(exc).lower()
            retryable_thread_error = (
                "message thread not found" in error_text
                or "message thread is not found" in error_text
                or "topic closed" in error_text
            )
            if message_thread_id is not None and retryable_thread_error:
                logger.warning(
                    "Telegram send failed for thread=%s; retrying without thread (%s)",
                    message_thread_id,
                    exc,
                )
                try:
                    return await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup,
                        disable_web_page_preview=disable_web_page_preview,
                        message_thread_id=None,
                    )
                except Exception as retry_exc:
                    logger.error(f"Telegram API call retry failed: {retry_exc}", exc_info=True)
                    return None
            logger.error(f"Telegram API call failed: {exc}", exc_info=True)
            return None

    def _setup_handlers(self) -> None:
        """Setup message and callback handlers."""
        self.dp.message.register(self._handle_start, Command("start"))
        self.dp.message.register(self._handle_pair, Command("pair"))
        self.dp.message.register(self._handle_autothread, Command("autothread"))
        self.dp.message.register(self._handle_help, Command("help"))
        self.dp.message.register(self._handle_status, Command("status"))
        self.dp.message.register(self._handle_queue, Command("queue"))
        self.dp.message.register(self._handle_candidate, Command("candidate"))
        self.dp.message.register(self._handle_deploys, Command("deploys"))
        self.dp.message.register(self._handle_wallets, Command("wallets"))
        self.dp.message.register(self._handle_setsigner, Command("setsigner"))
        self.dp.message.register(self._handle_setadmin, Command("setadmin"))
        self.dp.message.register(self._handle_setreward, Command("setreward"))
        self.dp.message.register(self._handle_manualdeploy, Command("manualdeploy"))
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
        self.dp.callback_query.register(self._handle_nav_status, F.data == "nav_control")
        self.dp.callback_query.register(self._handle_nav_status, F.data == "nav_home")
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
        # Manual Deployment Wizard
        self.wizard.register_handlers(self.dp)

        # Candidate details

    def _is_authorized_chat(self, chat_id: Any) -> bool:
        return str(chat_id) == str(self.chat_id)

    async def _set_bot_commands(self) -> None:
        """Publish the Golden 8 slash menu to Telegram."""
        await self.bot.set_my_commands(
            [
                BotCommand(command="status", description="Home Dashboard & Health"),
                BotCommand(command="queue", description="Pending Item Review"),
                BotCommand(command="wallets", description="Wallet Configuration"),
                BotCommand(command="manualdeploy", description="Launch Deployment Wizard"),
                BotCommand(command="pair", description="Bind Bot to Chat/Thread"),
                BotCommand(command="autothread", description="Auto-Create Topics"),
                BotCommand(command="panic", description="🚨 SAFETY: Force Review Mode"),
                BotCommand(command="help", description="Usage Guide"),
            ]
        )

    async def _ensure_forum_topics_bound(
        self,
        *,
        create_missing: bool = False,
    ) -> tuple[list[str], list[str]]:
        """Validate/bind forum topics and optionally create missing ones."""
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
        configured_bindings = {
            "review": _normalize_thread_id(self.thread_review_id),
            "deploy": _normalize_thread_id(self.thread_deploy_id),
            "claim": _normalize_thread_id(self.thread_claim_id),
            "ops": _normalize_thread_id(self.thread_ops_id),
            "alert": _normalize_thread_id(self.thread_alert_id),
        }
        for category, thread_id in configured_bindings.items():
            if thread_id is not None:
                current_bindings[category] = thread_id

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
                        if not create_missing:
                            failures.append(f"{category}: bound topic {existing_id} is missing/inaccessible")
                            continue
                        # If edit fails, it might be deleted. Fall through to create.
                        logger.debug(f"Failed to edit topic {existing_id} for {category}, will recreate.")
                elif not create_missing:
                    failures.append(f"{category}: not bound")
                    continue

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
                       reply_markup=_build_back_home_keyboard()
                  )
                  return
             
             if not thread_id:
                  await message.answer(
                       _fmt_dashboard_header("Notice", "⚠️") +
                       "Run <code>/pair &lt;cat&gt;</code> inside a topic.", 
                       parse_mode="HTML",
                       reply_markup=_build_back_home_keyboard()
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
                       reply_markup=_build_back_home_keyboard()
                  )
             except Exception as exc:
                  logger.warning(f"Failed renaming topic {thread_id} to {topic_title}: {exc}")
                  await message.answer(
                       _fmt_dashboard_header("Success", "✅") +
                       f"Bound to <b>{category}</b> (Rename failed - check permissions)", 
                       parse_mode="HTML",
                       reply_markup=_build_back_home_keyboard()
                  )
             return

        # Regular pairing (Ops binding)
        self._bind_dynamic_thread("ops", thread_id)
        created_line = "none"
        failure_block = (
            "\n\n<b>Auto Thread Setup:</b> disabled on /pair to prevent duplicate topics.\n"
            "Use <code>/pair &lt;category&gt;</code> inside each existing topic, or run <code>/autothread force</code> only when you really want to create missing topics."
        )
        await message.answer(
            _fmt_dashboard_header("Paired", "🔗") +
            f"• <b>Chat ID:</b> {_fmt_inline_code(self.chat_id)}\n"
            f"• <b>Topics created:</b> {_fmt_text(created_line)}\n"
            "Bot will now accept commands in this chat."
            f"{failure_block}",
            parse_mode="HTML",
            reply_markup=_build_back_home_keyboard(),
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

        created, failures = await self._ensure_forum_topics_bound(create_missing=force or bool(current_bindings))
        if failures:
            await message.answer(
                _fmt_dashboard_header("Auto Thread Setup Incomplete", "⚠️") +
                f"• <b>Created:</b> {_fmt_text(', '.join(created) if created else 'none')}\n"
                f"• <b>Errors:</b> {_fmt_text(' | '.join(failures[:5]))}\n\n"
                "For existing topics, use <code>/pair review</code>, <code>/pair deploy</code>, etc. Use <code>/autothread force</code> only to create new topics.",
                parse_mode="HTML",
                reply_markup=_build_dashboard_keyboard(),
            )
            return
        await message.answer(
            _fmt_dashboard_header("Auto Thread Setup Complete", "✅") +
            f"• <b>Created:</b> {_fmt_text(', '.join(created) if created else 'none')}",
            parse_mode="HTML",
            reply_markup=_build_back_home_keyboard(),
        )

    # ── command handlers ──────────────────────────────────────────────────────

    async def _handle_start(self, message: Message) -> None:
        """New User Onboarding: Mission Briefing."""
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        
        await self._show_ui_view(
            message,
            _fmt_dashboard_header("Mission Briefing", "🛰") +
            "<b>Welcome, Operator.</b>\n\n"
            "This bot manages the lifecycle of tactical token deployments.\n"
            "Follow these steps to begin operation:\n\n"
            "1. 📍 <b>Pair Topics</b>: Use <code>Settings > Pair Thread</code> to bind current threads to categories (Review, Deploy, etc).\n"
            "2. 🛂 <b>Review Signals</b>: Quality-filtered signals will appear in your <b>Review</b> thread.\n"
            "3. 🧪 <b>Execute</b>: Use <b>Manual Deploy</b> to launch custom metadata traps.\n\n"
            "<i>Click 🗺 Status below to see your command center.</i>",
            _build_dashboard_keyboard()
        )

    async def _show_ui_view(self, event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None) -> None:
        """Intelligent UI renderer that either edits (callback) or answers (message)."""
        if isinstance(event, CallbackQuery):
            try:
                 if event.message:
                      await event.message.edit_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
                 await event.answer()
            except Exception as exc:
                 logger.debug(f"UI Edit failed (stale?): {exc}")
        else:
            await event.answer(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

    async def _handle_help(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
             return
        await self._show_ui_view(
            message,
            _fmt_dashboard_header("Command Center", "🛰") +
             "<b>Operational Flow</b>\n"
             "• Click <b>Approval/Reject</b> on signals to execute.\n"
             "• Use <b>🧪 Edit & Deploy</b> to customize metadata.\n"
             "• Use <b>🛠 Tools</b> for advanced system configuration.\n\n"
             "<b>Quick Commands</b>\n"
             "• <code>/status</code>: Master Dashboard & Health.\n"
             "• <code>/queue</code>: Pending review items.\n"
             "• <code>/manualdeploy</code>: Launch the Wizard.",
             _build_dashboard_keyboard()
        )

    async def _handle_status(self, message: Message | CallbackQuery, state: FSMContext | None = None) -> None:
        """Unified Command Center: Stats + Ops Control + Health Check."""
        chat_id = message.chat.id if isinstance(message, Message) else message.message.chat.id
        if not self._is_authorized_chat(chat_id):
            return
        if state:
            await state.clear()
            
        thread_id = getattr(message, "message_thread_id", None) if isinstance(message, Message) else getattr(message.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)

        # 1. Health Checks
        is_review_paired = "review" in self._dynamic_thread_bindings
        is_deploy_paired = "deploy" in self._dynamic_thread_bindings
        
        health_alerts = ""
        if not is_review_paired:
            health_alerts += "🔴 <b>Review Thread:</b> Unpaired\n"
        if not is_deploy_paired:
            health_alerts += "🔴 <b>Deploy Thread:</b> Unpaired\n"

        detector_alerts = build_detector_health_alerts(self._runtime_get)
        if detector_alerts:
            health_alerts += detector_alerts + "\n"
        
        if health_alerts:
            health_alerts = "<b>⚠️ SYSTEM ALERTS:</b>\n" + health_alerts + "\n"

        # 2. Stats Block
        stats_block = ""
        if self._db:
            try:
                # Optimized: Run blocking DB call in background thread
                stats = await asyncio.to_thread(self._db.get_stats)
                stats_block = (
                    f"Pending: <b>{stats['pending_reviews']}</b> | Deployed: <b>{stats['deployed']}</b>\n"
                    f"Seen: <b>{stats['total_candidates']}</b> | Fails: <b>{stats['deploy_failed']}</b>\n\n"
                )
            except Exception as exc:
                logger.error(f"Error fetching status: {exc}")

        # 3. Ops Block
        mode = (self._runtime_get("ops.mode") or "review").strip().lower()
        bot_state = (self._runtime_get("ops.bot_enabled") or "on").strip().lower()
        deployer = (self._runtime_get("ops.deployer_mode") or "clanker").strip().lower()
        
        mode_view = "🟩 AUTO" if mode == "auto" else "🟦 REVIEW"
        bot_view = "🟢 ON" if bot_state in {"on", "true", "1", "yes"} else "🔴 OFF"
        
        text = (
            _fmt_dashboard_header("Master Dashboard", "⚙️") +
            health_alerts +
            stats_block +
            f"• <b>Mode:</b> {mode_view}\n"
            f"• <b>Bot:</b> {bot_view}\n"
            f"• <b>Deployer:</b> {deployer.upper()}\n\n"
            "<i>Pro-Tip: Use /status in any thread to go Home.</i>"
        )
        
        await self._show_ui_view(message, text, _build_dashboard_keyboard())

    async def _handle_queue(self, event: Message | CallbackQuery) -> None:
        chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
        if not self._is_authorized_chat(chat_id):
            return
        thread_id = getattr(event, "message_thread_id", None) if isinstance(event, Message) else getattr(event.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await self._show_ui_view(event, _fmt_dashboard_header("Notice", "ℹ️") + "Database not available.", _build_dashboard_keyboard())
            return
        try:
            # Optimized: Run blocking DB call in background thread
            rows = await asyncio.to_thread(self._db.list_pending_reviews)
            msg = build_queue_message(rows)
            await self._show_ui_view(event, msg, _build_dashboard_keyboard())
        except Exception as exc:
            logger.error(f"Error listing queue: {exc}", exc_info=True)
            await self._show_ui_view(event, _fmt_dashboard_header("Notice", "⚠️") + "Error fetching queue.", _build_dashboard_keyboard())

    async def _handle_control(self, message: Message, state: FSMContext | None = None) -> None:
        """Decommissioned /control in favor of Unified Dashboard."""
        await self._handle_status(message, state)

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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("ops.mode", mode):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Success", "✅") +
             f"Mode set to <b>{_fmt_text(mode)}</b>.", 
             parse_mode="HTML",
             reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("ops.auto_threshold", str(threshold)):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving threshold.",
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Threshold Updated", "🎯") +
             f"Auto-deploy threshold set to <b>{threshold}/100</b>.\n"
             f"Signals scoring ≥ {threshold} will be deployed automatically when in AUTO mode.",
             parse_mode="HTML",
             reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("PANIC MODE ACTIVATED", "🚨") +
             "🔴 <b>System is now in REVIEW mode.</b>\n\n"
             "All autonomous deployments have been halted.\n"
             "Every incoming signal will require <b>manual approval</b>.\n\n"
             "<i>Run /setmode auto to resume autonomous operation.</i>",
             parse_mode="HTML",
             reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        value = parts[1].strip().lower()
        if value not in {"on", "off"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid value. Use on or off.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("ops.bot_enabled", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving bot state.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Success", "✅") +
             f"Bot notifications set to <b>{_fmt_text(value)}</b>.", 
             parse_mode="HTML",
             reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        value = parts[1].strip().lower()
        if value not in {"clanker", "bankr", "both"}:
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid deployer mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("ops.deployer_mode", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving deployer mode.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return

        await message.answer(
            _fmt_dashboard_header("Success", "✅") +
            f"Deployer mode set to <b>{_fmt_text(value.upper())}</b>.",
            parse_mode="HTML",
            reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /candidate &lt;candidate_id&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )

    async def _handle_deploys(self, event: Message | CallbackQuery) -> None:
        chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
        if not self._is_authorized_chat(chat_id):
            return
        thread_id = getattr(event, "message_thread_id", None) if isinstance(event, Message) else getattr(event.message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        if not self._db:
            await self._show_ui_view(event, _fmt_dashboard_header("Notice", "ℹ️") + "Database not available.", _build_dashboard_keyboard())
            return
        try:
            # Optimized: Run blocking DB call in background thread
            rows = await asyncio.to_thread(self._db.list_recent_deployments, limit=10)
            msg = build_deploys_message(rows)
            await self._show_ui_view(event, msg, _build_dashboard_keyboard())
        except Exception as exc:
            logger.error(f"Error fetching deployments: {exc}", exc_info=True)
            await self._show_ui_view(event, _fmt_dashboard_header("Notice", "⚠️") + "Error fetching deployments.", _build_dashboard_keyboard())

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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        parts = (message.text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                 _fmt_dashboard_header("Usage", "❓") +
                 "Usage: /cancel &lt;candidate_id&gt;", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
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
                    reply_markup=_build_back_home_keyboard(),
                )
            else:
                await message.answer(
                    _fmt_dashboard_header("Notice", "⚠️") +
                    f"Could not cancel <code>{candidate_id}</code> — not found or already processed.",
                    parse_mode="HTML",
                    reply_markup=_build_back_home_keyboard()
                )
        except Exception as exc:
            logger.error(f"Error cancelling review: {exc}", exc_info=True)
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Error cancelling review.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
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
            reply_markup=_build_back_home_keyboard(),
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.deployer_signer"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting signer override.", 
                     parse_mode="HTML",
                     reply_markup=_build_back_home_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Signer Status", "⚙️") +
                 "Signer override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not (_is_evm_address(value) or _is_private_key(value)):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid signer. Use EVM address or 0x private key.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("wallet.deployer_signer", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving signer override.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
            _fmt_dashboard_header("Signer Status", "⚙️") +
            f"Signer override updated: {_fmt_inline_code(_mask_sensitive_wallet(value))}",
            parse_mode="HTML",
            reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.token_admin"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting token admin override.", 
                     parse_mode="HTML",
                     reply_markup=_build_back_home_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Admin Status", "⚙️") +
                 "Token admin override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not _is_evm_address(value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid token admin address.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("wallet.token_admin", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving token admin override.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Admin Settings", "⚙️") +
             f"✅ Token admin updated: {_fmt_inline_code(value)}", 
             parse_mode="HTML", 
             reply_markup=_build_back_home_keyboard()
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
                 reply_markup=_build_back_home_keyboard()
            )
            return
        value = parts[1].strip()
        if value.lower() in {"default", "clear", "reset"}:
            if not self._runtime_delete("wallet.fee_recipient"):
                await message.answer(
                     _fmt_dashboard_header("Notice", "⚠️") +
                     "Failed resetting reward recipient override.", 
                     parse_mode="HTML",
                     reply_markup=_build_back_home_keyboard()
                )
                return
            await message.answer(
                 _fmt_dashboard_header("Reward Status", "⚙️") +
                 "Reward recipient override reset to default.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not _is_evm_address(value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Invalid reward recipient address.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        if not self._runtime_set("wallet.fee_recipient", value):
            await message.answer(
                 _fmt_dashboard_header("Notice", "⚠️") +
                 "Failed saving reward recipient override.", 
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
            )
            return
        await message.answer(
             _fmt_dashboard_header("Reward Status", "⚙️") +
             f"Reward recipient updated: {_fmt_inline_code(value)}", 
             parse_mode="HTML",
             reply_markup=_build_back_home_keyboard()
        )

    async def _handle_manualdeploy(self, message: Message) -> None:
        if not self._is_authorized_chat(message.chat.id):
            return
        thread_id = getattr(message, "message_thread_id", None)
        self._capture_operator_thread(thread_id)
        self._bind_dynamic_thread("ops", thread_id)
        await message.answer(
            _fmt_dashboard_header("Manual Deploy Guide", "🧪") +
            "<b>Use wizard only (8 command set).</b>\n\n"
            "Flow:\n"
            "1. Open <b>Dashboard</b>\n"
            "2. Choose <b>Manual Deploy Wizard</b>\n"
            "3. Fill <b>platform, name, symbol, image, description</b>\n"
            "4. Tap <b>Launch Deployment</b>\n\n"
            "Notes:\n"
            "• No additional slash command is needed\n"
            "• Existing candidate deploy is available from candidate detail/edit",
            parse_mode="HTML",
            reply_markup=_build_back_home_keyboard(),
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
        if not self._db:
            await callback.answer("Database unavailable", show_alert=True)
            return
        try:
            detail_message = await self._render_candidate_detail(candidate_id)
            await callback.message.edit_text(
                detail_message,
                parse_mode="HTML",
                reply_markup=self._build_review_keyboard(candidate_id),
                disable_web_page_preview=True,
            )
            await callback.answer("Opened Detail")
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                await callback.answer("Detail already open")
                return
            logger.error("Error rendering candidate detail: %s", exc, exc_info=True)
            await callback.answer("Detail failed", show_alert=True)
        except Exception as exc:
            logger.error("Error rendering candidate detail: %s", exc, exc_info=True)
            await callback.answer("Detail failed", show_alert=True)

    def _resolve_candidate_brief(self, candidate_id: str) -> tuple[str, str, str, str]:
        network = "base"
        token_name = "Unknown"
        token_symbol = "N/A"
        if self._db:
            try:
                row = self._db.get_candidate(candidate_id)
                if row:
                    meta = json.loads(row["metadata_json"] or "{}")
                    network = str(meta.get("network") or network)
                    token_name = str(meta.get("token_name") or meta.get("suggested_name") or token_name)
                    token_symbol = str(meta.get("token_symbol") or meta.get("suggested_symbol") or token_symbol)
                    raw_text_val = _row_get(row, "raw_text")
                    if (token_name == "Unknown" or token_symbol == "N/A") and raw_text_val:
                        raw_text = str(raw_text_val)
                        matched = re.search(r":\s*([A-Za-z0-9 ._-]{2,50})\s*\(([A-Za-z0-9]{2,10})\)", raw_text)
                        if matched:
                            token_name = matched.group(1).strip() or token_name
                            token_symbol = matched.group(2).strip().upper() or token_symbol
            except Exception:
                pass
        return _network_icon(network), _fmt_text(token_name), _fmt_text(token_symbol), network

    async def _handle_nav_status(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Status."""
        await self._handle_status(callback, state)

    async def _handle_nav_queue(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Queue."""
        await state.clear()
        await self._handle_queue(callback)

    async def _handle_nav_deploys(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Dashboard Navigation: Quick jump to Recent Deploys."""
        await state.clear()
        await self._handle_deploys(callback)

    async def _handle_nav_control(self, callback: CallbackQuery) -> None:
        """Dashboard Navigation: Quick jump to Dashboard."""
        await self._handle_control(callback)

    async def _handle_nav_tools(self, callback: CallbackQuery) -> None:
        """Navigation: Show master command hub."""
        await self._show_ui_view(
            callback,
            _fmt_dashboard_header("System Tools", "🛠") +
            "Direct access to all bot operations and settings:",
            _build_tools_keyboard()
        )

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
            reply_markup=_build_back_home_keyboard()
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
            reply_markup=_build_back_home_keyboard()
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
            reply_markup=_build_back_home_keyboard()
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
            reply_markup=_build_back_home_keyboard()
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
                     f"• <b>TX:</b> <a href=\"https://basescan.org/tx/{result.tx_hash}\">{result.tx_hash[:10]}...{result.tx_hash[-6:]}</a>",
                     parse_mode="HTML",
                     disable_web_page_preview=True,
                     reply_markup=_build_back_home_keyboard()
                 )
            else:
                 await callback.message.edit_text(
                     _fmt_dashboard_header("Claim Failed", "❌") +
                     f"Failed claiming fees for <code>{address}</code>.\n"
                     f"Reason: <i>{result.error_message}</i>",
                     parse_mode="HTML",
                     reply_markup=_build_back_home_keyboard()
                 )
        except Exception as exc:
             await callback.message.edit_text(
                 _fmt_dashboard_header("Error", "⚠️") + f"Internal error during claim: {exc}",
                 parse_mode="HTML",
                 reply_markup=_build_back_home_keyboard()
             )
        await callback.answer()

    async def _handle_nav_help(self, callback: CallbackQuery) -> None:
        """Dashboard Navigation: Quick jump to Help."""
        if not callback.message:
             return
        await self._handle_help(callback.message)
        await callback.answer()


    # ── Notifications ─────────────────────────────────────────────────────────


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
                reply_markup=self._build_review_keyboard(candidate_id),
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
            meta = metadata or {}
            # Resolve context_url: explicit param → metadata fallback
            resolved_url = context_url or meta.get("context_url")
            # Resolve raw_text: explicit param → metadata fallback
            resolved_raw = raw_text or meta.get("raw_text")

            message_text = build_review_message(
                candidate_id,
                review_priority,
                score,
                reason_codes,
                raw_text=resolved_raw,
                source=source,
                context_url=resolved_url,
                author_handle=author_handle,
                metadata=meta,
            )
            keyboard = self._build_review_keyboard(candidate_id, context_url=resolved_url)

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

    async def send_deploy_preparing(
        self,
        candidate_id: str,
        *,
        token_name: str | None = None,
        token_symbol: str | None = None,
        network: str | None = None,
    ) -> None:
        """Notify that deploy preparation has started."""
        try:
            if token_name and token_symbol:
                resolved_name = _fmt_text(token_name)
                resolved_symbol = _fmt_text(token_symbol)
                resolved_network = str(network or "base").lower()
                icon = _network_icon(resolved_network)
            else:
                icon, resolved_name, resolved_symbol, _ = self._resolve_candidate_brief(candidate_id)
            await self._send_bot_message(
                text=(
                    "⚙️ <b>Deploying</b>\n"
                    f"{icon} <b>{resolved_name}</b> <code>${resolved_symbol}</code>\n"
                    "• <b>Stage:</b> prepare metadata + image IPFS\n"
                    f"• <b>Ref:</b> {_fmt_inline_code(_fmt_truncate(candidate_id, 24))}"
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
        *,
        token_name: str | None = None,
        token_symbol: str | None = None,
        network: str | None = None,
    ) -> None:
        """Send deploy success notification."""
        try:
            if token_name and token_symbol:
                resolved_name = _fmt_text(token_name)
                resolved_symbol = _fmt_text(token_symbol)
                resolved_network = str(network or "base").lower()
                icon = _network_icon(resolved_network)
            else:
                icon, resolved_name, resolved_symbol, resolved_network = self._resolve_candidate_brief(candidate_id)
            tx_url = html.escape(_get_explorer_url(resolved_network, "tx", tx_hash), quote=True)
            ca_url = html.escape(_get_explorer_url(resolved_network, "address", contract_address), quote=True)
            await self._send_bot_message(
                text=(
                    "✅ <b>Deploy Success</b>\n"
                    f"{icon} <b>{resolved_name}</b> <code>${resolved_symbol}</code>\n"
                    f"• <b>Contract:</b> <a href=\"{ca_url}\">{_fmt_inline_code(_fmt_truncate(contract_address, 14))}</a>\n"
                    f"• <b>TX:</b> <a href=\"{tx_url}\">{_fmt_inline_code(_fmt_truncate(tx_hash, 18))}</a>"
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
                    "❌ <b>Deploy Failed</b>\n"
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
        logger.info(
            "telegram.routing chat_id=%s review=%s deploy=%s claim=%s ops=%s alert=%s default=%s dynamic=%s",
            self.chat_id,
            self._thread_for("review"),
            self._thread_for("deploy"),
            self._thread_for("claim"),
            self._thread_for("ops"),
            self._thread_for("alert"),
            self._resolve_message_thread_id(),
            self._dynamic_thread_bindings,
        )
        try:
            await self._set_bot_commands()
        except Exception as exc:
            logger.warning("Failed setting slash commands: %s", exc)
        try:
            created, failures = await self._ensure_forum_topics_bound(create_missing=False)
            if created:
                logger.info("telegram.auto_thread_setup created=%s", ",".join(created))
            if failures:
                if failures == ["paired chat is not a forum supergroup"]:
                    logger.info("telegram.auto_thread_setup skipped: paired chat is not forum-enabled")
                else:
                    logger.info("telegram.auto_thread_setup pending=%s", " | ".join(failures))
        except Exception as exc:
            logger.warning("telegram.auto_thread_setup failed: %s", exc)
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("Stopping Telegram bot")
        await self.bot.session.close()
