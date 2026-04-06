"""Shared parsing utilities for social signals and token identity hints."""

from __future__ import annotations

import re
from typing import Iterable

_EVM_CA_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_CA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_CASHTAG_RE = re.compile(r"\$([A-Za-z0-9]{2,12})\b")
# Support Name (TICKER), Name [TICKER], Name {TICKER}, Name - TICKER, Name: TICKER
_TICKER_SEP_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9 _-]{1,48})\s*[\(\[\{:-]\s*\$?([A-Za-z0-9]{2,12})\s*[\)\]\}]?\b"
)
_SYMBOL_HINT_RE = re.compile(
    r"\b(?:symbol|ticker|ca|contract)\s*[:=-]?\s*\$?([A-Za-z0-9_-]{2,16})\b", flags=re.IGNORECASE
)
_TOKEN_NAME_RE = re.compile(
    r"\btoken\s+(?:name\s*)?[:=-]?\s*([A-Za-z][A-Za-z0-9 _-]{1,48})",
    flags=re.IGNORECASE,
)
_NAME_HINT_RE = re.compile(r"\bname\s*[:=-]?\s*([A-Za-z][A-Za-z0-9 _-]{1,48})", flags=re.IGNORECASE)
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,30})")

_CHAIN_HINTS = {
    "base": "base",
    "sol": "solana",
    "solana": "solana",
    "bsc": "bsc",
    "eth": "ethereum",
    "ethereum": "ethereum",
}


def _clean_name(value: str) -> str:
    cleaned = re.sub(r"\b(?:symbol|ticker|ca|contract)\b.*$", "", value, flags=re.IGNORECASE)
    # Remove emojis and special chars at ends
    cleaned = re.sub(r"[^\x00-\x7F]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n-_:;,.!@#$%^&*()[]{}<>|/\\")
    return cleaned[:50]


def _clean_symbol(value: str) -> str:
    symbol = value.strip()
    if symbol.startswith("$"):
        symbol = symbol[1:]
    symbol = re.sub(r"[^A-Za-z0-9]", "", symbol).upper()
    if 2 <= len(symbol) <= 10:
        return symbol
    return ""


def extract_mentions(raw_text: str, explicit_handles: Iterable[str] | None = None) -> list[str]:
    handles: list[str] = []
    if explicit_handles:
        handles.extend(str(item).lower().lstrip("@") for item in explicit_handles if str(item).strip())
    handles.extend(item.lower().lstrip("@") for item in _MENTION_RE.findall(raw_text))
    return sorted(set(h for h in handles if h))


def extract_chain_hints(raw_text: str) -> list[str]:
    lowered = raw_text.lower()
    found: list[str] = []
    for alias, canonical in _CHAIN_HINTS.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            found.append(canonical)
    return sorted(set(found))


def extract_contracts(raw_text: str) -> tuple[list[str], list[str]]:
    evm_contracts = sorted(set(_EVM_CA_RE.findall(raw_text)))
    sol_candidates = _SOL_CA_RE.findall(raw_text)
    # Reduce false-positives by requiring at least one digit and excluding Base64-like suffixes
    sol_contracts = sorted(
        set(
            token for token in sol_candidates 
            if any(ch.isdigit() for ch in token) and len(token) >= 32
        )
    )
    return evm_contracts, sol_contracts


def extract_symbol_hint(raw_text: str) -> str | None:
    symbol_hint = _SYMBOL_HINT_RE.search(raw_text)
    if symbol_hint:
        symbol = _clean_symbol(symbol_hint.group(1))
        if symbol:
            return symbol

    ticker_match = _TICKER_SEP_RE.search(raw_text)
    if ticker_match:
        symbol = _clean_symbol(ticker_match.group(2))
        if symbol:
            return symbol

    cashtag = _CASHTAG_RE.search(raw_text)
    if cashtag:
        symbol = _clean_symbol(cashtag.group(1))
        if symbol:
            return symbol
    return None


def extract_name_hint(raw_text: str, symbol_hint: str | None = None) -> str | None:
    ticker_match = _TICKER_SEP_RE.search(raw_text)
    if ticker_match:
        if not symbol_hint or _clean_symbol(ticker_match.group(2)) == symbol_hint:
            name = _clean_name(ticker_match.group(1))
            if name:
                return name

    token_name = _TOKEN_NAME_RE.search(raw_text)
    if token_name:
        name = _clean_name(token_name.group(1))
        if name:
            return name

    name_hint = _NAME_HINT_RE.search(raw_text)
    if name_hint:
        name = _clean_name(name_hint.group(1))
        if name:
            return name
    return None
