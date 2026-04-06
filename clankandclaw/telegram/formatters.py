import html
import re
import shlex
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


_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")

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


def _fmt_truncate(value: Any, length: int = 8) -> str:
    if value is None:
        return "n/a"
    text = str(value).strip()
    return f"{text[:length]}…" if len(text) > length else text


def _get_explorer_url(network: str | None, type: str, value: str) -> str:
    net = str(network or "base").lower()
    base = "https://basescan.org"
    if net in ("solana", "sol"): base = "https://solscan.io"
    elif net == "bsc": base = "https://bscscan.com"
    elif net in ("eth", "ethereum"): base = "https://etherscan.io"
    
    if type == "tx":
        return f"{base}/tx/{value}"
    return f"{base}/address/{value}"


def _fmt_dashboard_header(title: str, emoji: str) -> str:
    line = "═" * (max(2, 22 - len(title)))
    return f"<b>{emoji} {title.upper()} {line}</b>\n\n"
