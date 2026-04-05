from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from clankandclaw.models.token import SignalCandidate


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
    metadata: dict = {"proxy_mode": "direct_or_configured"}
    if context_url:
        metadata["context_url"] = context_url
    if author_handle:
        metadata["author_handle"] = author_handle
    # Capture tweet image if present (first media item)
    media = event.get("media") or []
    if media and isinstance(media, list) and media[0].get("url"):
        metadata["image_url"] = media[0]["url"]

    return SignalCandidate(
        id=f"x-{event['id']}",
        source="x",
        source_event_id=str(event["id"]),
        observed_at=_normalize_observed_at(event),
        raw_text=raw_text,
        author_handle=author_handle,
        context_url=context_url,
        fingerprint=fingerprint,
        metadata=metadata,
    )
