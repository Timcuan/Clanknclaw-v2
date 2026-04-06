from clankandclaw.core.detectors import farcaster_detector
from clankandclaw.core.detectors.farcaster_detector import normalize_farcaster_event


def test_normalize_farcaster_event_extracts_target_mentions_and_contract():
    candidate = normalize_farcaster_event(
        {
            "id": "fc1",
            "text": "@bankr launch $MOON CA 0x1234567890abcdef1234567890abcdef12345678",
            "author": {"username": "alice"},
            "created_at": "2026-04-05T12:00:00Z",
            "mentioned_handles": ["bankr"],
            "like_count": 10,
            "recast_count": 3,
            "reply_count": 2,
        },
        "https://warpcast.com/~/conversations/fc1",
    )
    assert candidate.source == "farcaster"
    assert candidate.suggested_symbol == "MOON"
    assert candidate.metadata["fc_target_mention"] is True
    assert candidate.metadata["has_contract"] is True
    assert "bankr" in candidate.metadata["target_mentions"]


def test_normalize_farcaster_event_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(farcaster_detector, "_utc_now_iso", lambda: "2026-04-05T01:02:03Z")
    candidate = normalize_farcaster_event(
        {
            "id": "fc2",
            "text": "@clanker deploy",
            "author": {"username": "bob"},
            "mentioned_handles": ["clanker"],
        },
        "https://warpcast.com/~/conversations/fc2",
    )
    assert candidate.observed_at == "2026-04-05T01:02:03Z"


def test_normalize_farcaster_event_extracts_name_and_symbol_hints():
    candidate = normalize_farcaster_event(
        {
            "id": "fc3",
            "text": "@bankr token name: Moon Runner ticker: moon",
            "author": {"username": "eve"},
            "mentioned_handles": ["bankr"],
        },
        "https://warpcast.com/~/conversations/fc3",
    )
    assert candidate.suggested_name == "Moon Runner"
    assert candidate.suggested_symbol == "MOON"
