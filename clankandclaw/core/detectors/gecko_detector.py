from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from clankandclaw.models.token import SignalCandidate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_observed_at(payload: dict[str, Any]) -> str:
    for key in ("observed_at", "timestamp", "created_at", "published_at", "posted_at", "time"):
        value = payload.get(key)
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
                raise ValueError("payload timestamp must be timezone-aware")
            return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _utc_now_iso()


def normalize_gecko_payload(payload: dict, context_url: str) -> SignalCandidate:
    raw_text = payload["text"]
    fingerprint = sha256(f"gecko:{payload['id']}:{raw_text}".encode()).hexdigest()
    author_handle = payload.get("author")
    token_data = payload.get("token_data") or {}
    image_url = (
        token_data.get("image_url")
        or token_data.get("logo")
        or token_data.get("logo_url")
        or token_data.get("image")
        or token_data.get("logo_uri")
        or token_data.get("thumb")
        or token_data.get("large")
        or None
    )
    suggested_name = (token_data.get("name") or "").strip()[:50] or None
    suggested_symbol = (token_data.get("symbol") or "").strip()[:10].upper() or None
    description = (token_data.get("description") or "").strip()
    websites = token_data.get("websites") or []
    socials = token_data.get("socials") or {}

    metadata: dict[str, Any] = {"collector_mode": "direct_geckoterminal"}
    if context_url:
        metadata["context_url"] = context_url
    if author_handle:
        metadata["author_handle"] = author_handle
    if image_url:
        metadata["image_url"] = image_url
    if suggested_name:
        metadata["suggested_name"] = suggested_name
    if suggested_symbol:
        metadata["suggested_symbol"] = suggested_symbol
    if description:
        metadata["description"] = description
    if websites:
        metadata["websites"] = websites
    if socials:
        metadata["socials"] = socials
    for key in (
        "network",
        "dex",
        "dex_id",
        "volume",
        "transactions",
        "liquidity_usd",
        "pool_created_at",
        "spike_ratio",
        "spike_ratio_m1_m5",
        "hot_score",
        "confidence_tier",
        "gate_stage",
        "source_match_score",
        "source_tags_matched",
    ):
        value = payload.get(key)
        if value is not None:
            metadata[key] = value

    return SignalCandidate(
        id=f"gecko-{payload['id']}",
        source="gecko",
        source_event_id=str(payload["id"]),
        observed_at=_normalize_observed_at(payload),
        raw_text=raw_text,
        author_handle=author_handle,
        context_url=context_url,
        fingerprint=fingerprint,
        suggested_name=suggested_name,
        suggested_symbol=suggested_symbol,
        metadata=metadata,
    )
