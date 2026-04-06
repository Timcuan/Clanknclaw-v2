from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any

from clankandclaw.models.token import SignalCandidate
from clankandclaw.utils.parsing import (
    extract_chain_hints,
    extract_contracts,
    extract_mentions,
    extract_name_hint,
    extract_symbol_hint,
)
_TARGET_HANDLES = {"bankrbot", "clankerdeploy"}


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


def normalize_x_event(event: dict, context_url: str) -> SignalCandidate:
    raw_text = event["text"]
    fingerprint = sha256(f"x:{event['id']}:{raw_text}".encode()).hexdigest()
    author_handle = event.get("user", {}).get("username")
    lowered = raw_text.lower()

    mentioned_handles = [
        str((item or {}).get("username") or "")
        for item in (event.get("mentioned_users") or [])
    ]
    mention_set = extract_mentions(raw_text, mentioned_handles)
    target_mentions = sorted([h for h in mention_set if h in _TARGET_HANDLES])
    has_target_mention = bool(target_mentions)

    evm_contracts, sol_contracts = extract_contracts(raw_text)
    suggested_symbol = extract_symbol_hint(raw_text)
    suggested_name = extract_name_hint(raw_text, suggested_symbol)
    chain_hints = extract_chain_hints(raw_text)

    intent_keywords = ["deploy", "launch", "contract", "ca", "pair", "lp", "mint"]
    x_intent_score = sum(1 for kw in intent_keywords if re.search(rf"\b{re.escape(kw)}\b", lowered))
    engagement = (
        int(event.get("like_count") or 0)
        + (2 * int(event.get("retweet_count") or 0))
        + (2 * int(event.get("reply_count") or 0))
        + (2 * int(event.get("quote_count") or 0))
    )

    metadata: dict = {"proxy_mode": "direct_or_configured"}
    if context_url:
        metadata["context_url"] = context_url
    if author_handle:
        metadata["author_handle"] = author_handle
    if mention_set:
        metadata["mentioned_handles"] = mention_set
    if target_mentions:
        metadata["target_mentions"] = target_mentions
    metadata["x_target_mention"] = has_target_mention
    metadata["x_intent_score"] = x_intent_score
    metadata["x_engagement_score"] = engagement
    metadata["like_count"] = int(event.get("like_count") or 0)
    metadata["retweet_count"] = int(event.get("retweet_count") or 0)
    metadata["reply_count"] = int(event.get("reply_count") or 0)
    metadata["quote_count"] = int(event.get("quote_count") or 0)
    metadata["view_count"] = int(event.get("view_count") or 0)
    metadata["conversation_id"] = event.get("conversation_id")
    metadata["in_reply_to_tweet_id"] = event.get("in_reply_to_tweet_id")
    if chain_hints:
        metadata["chain_hints"] = sorted(set(chain_hints))
    if evm_contracts:
        metadata["evm_contracts"] = sorted(set(evm_contracts))
    if sol_contracts:
        metadata["sol_contracts"] = sorted(set(sol_contracts))
    metadata["has_contract"] = bool(evm_contracts or sol_contracts)
    # Capture tweet images correctly (handle common API variants)
    media_objs = []
    # 1. Extended Entities (often contains high-res)
    ext_ent = event.get("extended_entities", {}) 
    media_objs.extend(ext_ent.get("media") or [])
    # 2. Standard Entities
    media_objs.extend(event.get("entities", {}).get("media") or [])
    # 3. Direct media field (often provided by wrappers)
    media_objs.extend(event.get("media") or [])

    image_urls = []
    seen_urls = set()
    for item in media_objs:
        if not isinstance(item, dict):
             continue
        url = item.get("media_url_https") or item.get("media_url") or item.get("url")
        if url and isinstance(url, str) and url not in seen_urls:
            image_urls.append(url)
            seen_urls.add(url)

    if image_urls:
        metadata["image_url"] = image_urls[0]
        metadata["image_candidates"] = image_urls

    return SignalCandidate(
        id=f"x-{event['id']}",
        source="x",
        source_event_id=str(event["id"]),
        observed_at=_normalize_observed_at(event),
        raw_text=raw_text,
        author_handle=author_handle,
        context_url=context_url,
        suggested_name=suggested_name,
        suggested_symbol=suggested_symbol,
        fingerprint=fingerprint,
        metadata=metadata,
    )
