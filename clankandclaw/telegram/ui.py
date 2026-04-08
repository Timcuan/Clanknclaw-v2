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


_SIGNAL_MAP: dict[str, str] = {
    # Pipeline: volume
    "gecko_volume_m5_strong": "🌊 Vol Strong",
    "gecko_volume_m5_ok": "📊 Vol OK",
    "gecko_volume_m5_light": "📉 Vol Light",
    "gecko_volume_m15_strong": "🌊 15m Strong",
    "gecko_volume_m15_ok": "📈 15m OK",
    "gecko_volume_m1_strong": "⚡ Burst Strong",
    "gecko_volume_m1_ok": "⚡ Burst OK",
    # Pipeline: TX
    "gecko_tx_m5_strong": "🔥 TX Surge",
    "gecko_tx_m5_ok": "⚡ Active TX",
    "gecko_tx_m5_light": "👀 Low TX",
    "gecko_tx_m1_strong": "🚀 Instant Surge",
    "gecko_tx_m1_ok": "⚡ New Buys",
    # Pipeline: liquidity
    "gecko_liquidity_strong": "💰 Deep Liq",
    "gecko_liquidity_ok": "💧 Liq OK",
    "gecko_liquidity_light": "💧 Liq Light",
    # Pipeline: spike
    "gecko_spike_ratio_strong": "🚀 Strong Spike",
    "gecko_spike_ratio_ok": "📈 Spike",
    "gecko_spike_m1_m5_healthy": "🎯 Healthy Burst",
    "gecko_spike_m1_m5_ok": "📊 M1 OK",
    # Pipeline: buy pressure (most important)
    "gecko_buy_pressure_strong": "💚 Buy Heavy",
    "gecko_buy_pressure_ok": "💚 Buyers OK",
    # Pipeline: confidence
    "gecko_confidence_high": "💎 High Conf",
    "gecko_confidence_medium": "⚖️ Med Conf",
    # Pipeline: age/launch tier
    "gecko_ultra_fresh": "⚡ Ultra Fresh",
    "gecko_very_fresh": "🆕 Very Fresh",
    "gecko_fresh": "🆕 Fresh",
    # Pipeline: mode
    "gecko_trending_signal": "📈 Trending",
    "gecko_hot_gate": "🔥 Alpha Gate",
    "gecko_hot_gate_ok": "🔥 Hot",
    # Network
    "network_base": "🔵 Base",
    "network_eth": "🔷 ETH",
    "network_solana": "🟣 Solana",
    "network_bsc": "🟡 BSC",
    # Source
    "base_score": "📊 Score",
    "base_target_source": "🎯 Target DEX",
    # Detector signals (dual-mode)
    "gecko_new_launch": "🆕 New Launch",
    "gecko_trending": "📈 Trending",
    "gecko_buy_pressure": "💚 Buy Heavy",
    "gecko_fresh_launch": "⚡ Fresh",
    "gecko_volume_surge": "🌊 Vol Surge",
}


