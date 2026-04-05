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


def normalize_gmgn_payload(payload: dict, context_url: str) -> SignalCandidate:
    raw_text = payload["text"]
    fingerprint = sha256(f"gmgn:{payload['id']}:{raw_text}".encode()).hexdigest()
    author_handle = payload.get("author")
    token_data = payload.get("token_data") or {}
    image_url = (
        token_data.get("image_uri")
        or token_data.get("logo")
        or token_data.get("image")
        or token_data.get("logo_uri")
        or None
    )

    metadata: dict = {"collector_mode": "remote_or_proxied"}
    if context_url:
        metadata["context_url"] = context_url
    if author_handle:
        metadata["author_handle"] = author_handle
    if image_url:
        metadata["image_url"] = image_url

    return SignalCandidate(
        id=f"gmgn-{payload['id']}",
        source="gmgn",
        source_event_id=str(payload["id"]),
        observed_at=_normalize_observed_at(payload),
        raw_text=raw_text,
        author_handle=author_handle,
        context_url=context_url,
        fingerprint=fingerprint,
        metadata=metadata,
    )
