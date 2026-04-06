import html
from typing import Any


def _source_label(source: str | None) -> str:
    return {
        "x": "X / Twitter",
        "farcaster": "Farcaster",
        "gecko": "GeckoTerminal",
        "gmgn": "GMGN Smart Money",
    }.get(source or "", source or "unknown")


def _network_icon(network: str | None) -> str:
    net = str(network or "").lower()
    if net in ("solana", "sol"): return "🟣"
    if net == "base": return "🔵"
    if net == "bsc": return "🟡"
    if net in ("eth", "ethereum"): return "🔷"
    return "🌐"


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


def _fmt_dashboard_header(title: str, emoji: str) -> str:
    line = "═" * (max(2, 22 - len(title)))
    return f"<b>{emoji} {title.upper()} {line}</b>\n\n"
