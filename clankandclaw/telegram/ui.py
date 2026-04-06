import html
import json
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
else:
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        AIOGRAM_AVAILABLE = True
    except ImportError:
        AIOGRAM_AVAILABLE = False
        InlineKeyboardMarkup = Any
        InlineKeyboardButton = Any

from clankandclaw.telegram.formatters import (
    _fmt_text, _fmt_inline_code, _fmt_num, _fmt_truncate, 
    _get_explorer_url, _source_label, _network_icon, _fmt_dashboard_header
)

_MAX_RAW_TEXT = 300
_MAX_QUEUE_ITEMS = 10
_MAX_REASONS = 6
_MAX_CALLBACK_DATA = 64
_THREAD_CATEGORIES = ("review", "deploy", "claim", "ops", "alert")
_DEFAULT_FORUM_TOPIC_TITLES = {
    "review": "cnc-review",
    "deploy": "cnc-deploy",
    "claim": "cnc-claim",
    "ops": "cnc-ops",
    "alert": "cnc-alert",
}

def _build_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Home Hub: The absolute entry point for operators."""
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

def _build_back_home_keyboard() -> InlineKeyboardMarkup:
    """Lean contextual navigation: only shows Home."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Home Dashboard", callback_data="nav_home")]
        ]
    )

def _build_tools_keyboard() -> InlineKeyboardMarkup:
    """Categorized Action Hub."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Mode", callback_data="nav_tools_mode"),
                InlineKeyboardButton(text="🤖 Bot", callback_data="nav_tools_bot"),
                InlineKeyboardButton(text="🏗 Plat", callback_data="nav_tools_plat"),
            ],
            [
                InlineKeyboardButton(text="💸 Claim", callback_data="nav_tools_claim"),
                InlineKeyboardButton(text="🔐 Wallets", callback_data="nav_tools_wallets"),
            ],
            [
                InlineKeyboardButton(text="📍 Pair Thread", callback_data="nav_tools_pair"),
                InlineKeyboardButton(text="⚡ Discovery", callback_data="nav_tools_auto"),
            ],
            [
                InlineKeyboardButton(text="🏠 Home Dashboard", callback_data="nav_home"),
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
                InlineKeyboardButton(text="↩️ Back to Settings", callback_data="nav_tools"),
                InlineKeyboardButton(text="🏠 Home", callback_data="nav_home"),
            ]
        ]
    )

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
    plan = []
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
    network = _fmt_text(metadata.get("network"), fallback="unknown")
    dex_id = _fmt_text(metadata.get("dex_id"), fallback="unknown")
    confidence_tier = _fmt_text(metadata.get("confidence_tier"), fallback="n/a")
    gate_stage = _fmt_text(metadata.get("gate_stage"), fallback="n/a")
    liquidity_usd = _fmt_num(metadata.get("liquidity_usd"), digits=2, fallback="0.00")
    volume = metadata.get("volume") or {}
    tx_data = metadata.get("transactions") or {}
    
    volume_m5 = _fmt_num(volume.get("m5"), digits=2, fallback="0.00")
    volume_m15 = _fmt_num(volume.get("m15"), digits=2, fallback="0.00")
    volume_h1 = _fmt_num(volume.get("h1"), digits=2, fallback="0.00")
    
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

    if metadata.get("ai_enriched"):
        bullish = metadata.get("ai_bullish_score", 0)
        rationale = metadata.get("ai_rationale", "No rationale provided.")
        mood = "💎" if bullish >= 80 else "🔥" if bullish >= 60 else "⚖️" if bullish >= 40 else "⚠️"
        lines.append(f"\n{mood} <b>AI INSIGHT | {bullish}% BULLISH</b>")
        lines.append(f"<i>{_fmt_text(rationale)}</i>")

    fee_type = metadata.get("fee_type", "static").lower()
    fee_label = "10% STATIC" if fee_type == "static" else "1-10% DYNAMIC"
    protection_line = f"🛡️ <b>Anti-Sniper:</b> ENABLED (15s decay) | 📈 <b>Fees:</b> {fee_label}"
    lines.append(f"\n{protection_line}")

    if raw_text:
        trimmed = _fmt_truncate(raw_text, 60)
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
            f"  signals: {_fmt_text(_fmt_truncate(str(reasons), 120), fallback='—')}"
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
    """Build compact recent deployments message with clickable links."""
    if not rows:
        return "📭 No deployments yet."

    lines = [f"Total: <b>{len(rows)}</b>", ""]
    for row in rows:
        cid = str(row["candidate_id"] or "n/a")
        net = "base"
        if "solana" in cid.lower(): net = "solana"
        elif "bsc" in cid.lower(): net = "bsc"
        elif "eth" in cid.lower(): net = "eth"

        short_id = _fmt_truncate(cid, 8)
        
        if row["status"] == "deploy_success":
            contract = row["contract_address"]
            tx = row["tx_hash"]
            ca_link = f"<a href='{_get_explorer_url(net, 'address', contract)}'>[CA]</a>" if contract else "[CA]"
            tx_link = f"<a href='{_get_explorer_url(net, 'tx', tx)}'>[TX]</a>" if tx else "[TX]"
            lines.append(f"✅ {short_id} | {ca_link} | {tx_link}")
            continue

        error_code = row["error_code"] or "fail"
        raw_msg = (row["error_message"] or "").strip()
        if raw_msg.startswith("[") or raw_msg.startswith("{"):
            err_summary = "SDK Error"
        else:
            err_summary = raw_msg.split("\n")[0][:30]
            if len(raw_msg) > 30: err_summary += "…"
            
        lines.append(f"❌ {short_id} | <code>{error_code}</code>" + (f": {err_summary}" if err_summary else ""))

    return "\n".join(lines)

def build_review_keyboard(
    candidate_id: str,
    *,
    encode_candidate_id: Callable[[str], str] | None = None,
    mode: str = "summary",
) -> InlineKeyboardMarkup:
    """Build inline keyboard for operator actions."""
    if not AIOGRAM_AVAILABLE:
        raise ImportError("aiogram is required for keyboard building")

    if mode == "detail":
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Approve",
                        callback_data=build_action_callback_data("approve", candidate_id, encode_candidate_id=encode_candidate_id),
                    ),
                    InlineKeyboardButton(
                        text="❌ Reject",
                        callback_data=build_action_callback_data("reject", candidate_id, encode_candidate_id=encode_candidate_id),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🧪 Edit & Deploy",
                        callback_data=build_action_callback_data("wiz_edit", candidate_id, encode_candidate_id=encode_candidate_id),
                    ),
                    InlineKeyboardButton(
                        text="⬅️ Back",
                        callback_data=build_action_callback_data("refresh", candidate_id, encode_candidate_id=encode_candidate_id),
                    ),
                ],
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Approve",
                    callback_data=build_action_callback_data("approve", candidate_id, encode_candidate_id=encode_candidate_id),
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=build_action_callback_data("reject", candidate_id, encode_candidate_id=encode_candidate_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Detail",
                    callback_data=build_action_callback_data("detail", candidate_id, encode_candidate_id=encode_candidate_id),
                ),
                InlineKeyboardButton(
                    text="🧪 Edit & Deploy",
                    callback_data=build_action_callback_data("wiz_edit", candidate_id, encode_candidate_id=encode_candidate_id),
                ),
            ],
        ]
    )
