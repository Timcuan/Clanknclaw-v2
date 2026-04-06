from clankandclaw.core.detectors import x_detector
from clankandclaw.core.detectors.x_detector import normalize_x_event


def test_normalize_x_event_uses_source_timestamp_and_normalizes_fields():
    candidate = normalize_x_event(
        {
            "id": "1",
            "text": "deploy Pepe symbol PEPE",
            "created_at": "2026-04-04T12:34:56Z",
            "user": {"username": "alice"},
        },
        "https://x.example/1",
    )

    assert candidate.source == "x"
    assert candidate.source_event_id == "1"
    assert candidate.observed_at == "2026-04-04T12:34:56Z"
    assert candidate.context_url == "https://x.example/1"
    assert candidate.author_handle == "alice"
    assert candidate.metadata["proxy_mode"] == "direct_or_configured"
    assert candidate.metadata["context_url"] == "https://x.example/1"
    assert candidate.metadata["author_handle"] == "alice"
    assert len(candidate.fingerprint) == 64
    int(candidate.fingerprint, 16)


def test_normalize_x_event_captures_media_image_url():
    candidate = normalize_x_event(
        {
            "id": "3",
            "text": "deploy Moon token MOON",
            "user": {"username": "bob"},
            "media": [
                {"url": "https://pbs.twimg.com/media/example.jpg"},
                {"url": "https://pbs.twimg.com/media/example2.jpg"},
            ],
        },
        "https://x.example/3",
    )
    assert candidate.metadata["image_url"] == "https://pbs.twimg.com/media/example.jpg"
    assert candidate.metadata["image_candidates"] == [
        "https://pbs.twimg.com/media/example.jpg",
        "https://pbs.twimg.com/media/example2.jpg",
    ]


def test_normalize_x_event_extracts_mentions_contract_and_symbol():
    candidate = normalize_x_event(
        {
            "id": "4",
            "text": "@bankrbot deploy $MOON CA 0x1234567890abcdef1234567890abcdef12345678 on base",
            "user": {"username": "carol"},
            "mentioned_users": [{"username": "bankrbot"}],
            "like_count": 9,
            "retweet_count": 2,
            "reply_count": 3,
            "quote_count": 1,
        },
        "https://x.example/4",
    )
    assert candidate.suggested_symbol == "MOON"
    assert candidate.metadata["x_target_mention"] is True
    assert candidate.metadata["has_contract"] is True
    assert "bankrbot" in candidate.metadata["target_mentions"]
    assert "0x1234567890abcdef1234567890abcdef12345678" in candidate.metadata["evm_contracts"]
    assert "base" in candidate.metadata["chain_hints"]
    assert candidate.metadata["x_engagement_score"] > 0


def test_normalize_x_event_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(x_detector, "_utc_now_iso", lambda: "2026-04-04T01:02:03Z")

    candidate = normalize_x_event(
        {"id": "2", "text": "deploy Pepe symbol PEPE", "user": {"username": "bob"}},
        "https://x.example/2",
    )

    assert candidate.observed_at == "2026-04-04T01:02:03Z"


def test_normalize_x_event_extracts_symbol_without_cashtag():
    candidate = normalize_x_event(
        {
            "id": "5",
            "text": "deploy token moon symbol pepe",
            "user": {"username": "dave"},
        },
        "https://x.example/5",
    )
    assert candidate.suggested_symbol == "PEPE"
