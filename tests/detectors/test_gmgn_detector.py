from clankandclaw.core.detectors import gmgn_detector
from clankandclaw.core.detectors.gmgn_detector import normalize_gmgn_payload


def test_normalize_gmgn_payload_uses_source_timestamp_and_normalizes_fields():
    candidate = normalize_gmgn_payload(
        {
            "id": "g1",
            "text": "launch Pepe on Base",
            "timestamp": "2026-04-04T12:34:56Z",
            "author": "gmgn",
        },
        "https://gmgn.ai/token/g1",
    )

    assert candidate.source == "gmgn"
    assert candidate.source_event_id == "g1"
    assert candidate.observed_at == "2026-04-04T12:34:56Z"
    assert candidate.context_url == "https://gmgn.ai/token/g1"
    assert candidate.author_handle == "gmgn"
    assert candidate.metadata == {"collector_mode": "remote_or_proxied"}
    assert len(candidate.fingerprint) == 64
    int(candidate.fingerprint, 16)


def test_normalize_gmgn_payload_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(gmgn_detector, "_utc_now_iso", lambda: "2026-04-04T01:02:03Z")

    candidate = normalize_gmgn_payload(
        {"id": "g2", "text": "launch Pepe on Base", "author": "gmgn"},
        "https://gmgn.ai/token/g2",
    )

    assert candidate.observed_at == "2026-04-04T01:02:03Z"
