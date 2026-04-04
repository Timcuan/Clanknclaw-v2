from hashlib import sha256

from clankandclaw.models.token import SignalCandidate


def normalize_gmgn_payload(payload: dict, context_url: str) -> SignalCandidate:
    raw_text = payload["text"]
    fingerprint = sha256(f"gmgn:{payload['id']}:{raw_text}".encode()).hexdigest()
    return SignalCandidate(
        id=f"gmgn-{payload['id']}",
        source="gmgn",
        source_event_id=str(payload["id"]),
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle=payload.get("author"),
        context_url=context_url,
        fingerprint=fingerprint,
        metadata={"collector_mode": "remote_or_proxied"},
    )
