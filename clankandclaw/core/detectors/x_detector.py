from hashlib import sha256

from clankandclaw.models.token import SignalCandidate


def normalize_x_event(event: dict, context_url: str) -> SignalCandidate:
    raw_text = event["text"]
    fingerprint = sha256(f"x:{event['id']}:{raw_text}".encode()).hexdigest()
    return SignalCandidate(
        id=f"x-{event['id']}",
        source="x",
        source_event_id=str(event["id"]),
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle=event.get("user", {}).get("username"),
        context_url=context_url,
        fingerprint=fingerprint,
        metadata={"proxy_mode": "direct_or_configured"},
    )
