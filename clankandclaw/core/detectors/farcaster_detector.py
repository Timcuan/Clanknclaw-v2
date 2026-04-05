from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any

from clankandclaw.models.token import SignalCandidate

_EVM_CA_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_CA_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_RE = re.compile(r"\$([A-Za-z0-9]{2,10})\b")
_TARGET_HANDLES = {"bankr", "clanker"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_observed_at(event: dict[str, Any]) -> str:
    for key in ("observed_at", "created_at", "timestamp", "published_at", "posted_at", "time"):
        value = event.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if isinstance(value, str):
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("event timestamp must be timezone-aware")
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _utc_now_iso()


def normalize_farcaster_event(event: dict, context_url: str) -> SignalCandidate:
    raw_text = event["text"]
    fingerprint = sha256(f"farcaster:{event['id']}:{raw_text}".encode()).hexdigest()
    author_handle = event.get("author", {}).get("username")
    lowered = raw_text.lower()

    mentioned_handles = [
        str(item).lower().lstrip("@")
        for item in (event.get("mentioned_handles") or [])
        if str(item).strip()
    ]
    inline_handles = [h.lower().lstrip("@") for h in re.findall(r"@([A-Za-z0-9_]{1,30})", raw_text)]
    mention_set = sorted(set(mentioned_handles + inline_handles))
    target_mentions = sorted([h for h in mention_set if h in _TARGET_HANDLES])
    has_target_mention = bool(target_mentions)

    evm_contracts = _EVM_CA_RE.findall(raw_text)
    sol_contracts = _SOL_CA_RE.findall(raw_text)
    symbol_match = _SYMBOL_RE.search(raw_text)
    suggested_symbol = symbol_match.group(1).upper() if symbol_match else None

    chain_hints: list[str] = []
    for chain in ("base", "sol", "solana", "bsc", "eth", "ethereum"):
        if re.search(rf"\b{re.escape(chain)}\b", lowered):
            chain_hints.append(chain)

    intent_keywords = ["deploy", "launch", "contract", "ca", "pair", "lp", "mint"]
    intent_score = sum(1 for kw in intent_keywords if re.search(rf"\b{re.escape(kw)}\b", lowered))
    engagement_score = (
        int(event.get("like_count") or 0)
        + (2 * int(event.get("recast_count") or 0))
        + (2 * int(event.get("reply_count") or 0))
    )

    metadata: dict[str, Any] = {"collector_mode": "farcaster_api"}
    if context_url:
        metadata["context_url"] = context_url
    if author_handle:
        metadata["author_handle"] = author_handle
    metadata["mentioned_handles"] = mention_set
    metadata["target_mentions"] = target_mentions
    metadata["fc_target_mention"] = has_target_mention
    metadata["fc_intent_score"] = intent_score
    metadata["fc_engagement_score"] = engagement_score
    metadata["like_count"] = int(event.get("like_count") or 0)
    metadata["recast_count"] = int(event.get("recast_count") or 0)
    metadata["reply_count"] = int(event.get("reply_count") or 0)
    metadata["has_contract"] = bool(evm_contracts or sol_contracts)
    if evm_contracts:
        metadata["evm_contracts"] = sorted(set(evm_contracts))
    if sol_contracts:
        metadata["sol_contracts"] = sorted(set(sol_contracts))
    if chain_hints:
        metadata["chain_hints"] = sorted(set(chain_hints))

    return SignalCandidate(
        id=f"farcaster-{event['id']}",
        source="farcaster",
        source_event_id=str(event["id"]),
        observed_at=_normalize_observed_at(event),
        raw_text=raw_text,
        author_handle=author_handle,
        context_url=context_url,
        suggested_symbol=suggested_symbol,
        fingerprint=fingerprint,
        metadata=metadata,
    )
