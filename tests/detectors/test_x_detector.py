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
            "media": [{"url": "https://pbs.twimg.com/media/example.jpg"}],
        },
        "https://x.example/3",
    )
    assert candidate.metadata["image_url"] == "https://pbs.twimg.com/media/example.jpg"


def test_normalize_x_event_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(x_detector, "_utc_now_iso", lambda: "2026-04-04T01:02:03Z")

    candidate = normalize_x_event(
        {"id": "2", "text": "deploy Pepe symbol PEPE", "user": {"username": "bob"}},
        "https://x.example/2",
    )

    assert candidate.observed_at == "2026-04-04T01:02:03Z"
