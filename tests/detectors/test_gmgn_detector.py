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
    assert candidate.metadata["collector_mode"] == "remote_or_proxied"
    assert candidate.metadata["context_url"] == "https://gmgn.ai/token/g1"
    assert candidate.metadata["author_handle"] == "gmgn"
    assert len(candidate.fingerprint) == 64
    int(candidate.fingerprint, 16)


def test_normalize_gmgn_payload_extracts_image_url_from_token_data():
    candidate = normalize_gmgn_payload(
        {
            "id": "g3",
            "text": "launch Moon on Base",
            "author": "gmgn",
            "token_data": {"logo": "https://example.com/logo.png"},
        },
        "https://gmgn.ai/token/g3",
    )
    assert candidate.metadata["image_url"] == "https://example.com/logo.png"


def test_normalize_gmgn_payload_extracts_suggested_name_symbol_from_token_data():
    candidate = normalize_gmgn_payload(
        {
            "id": "g4",
            "text": "New token launch on Base chain: Moon (MOON)",
            "author": "gmgn",
            "token_data": {"name": "Moon", "symbol": "MOON"},
        },
        "https://gmgn.ai/base/token/g4",
    )
    assert candidate.suggested_name == "Moon"
    assert candidate.suggested_symbol == "MOON"
    assert candidate.metadata["suggested_name"] == "Moon"
    assert candidate.metadata["suggested_symbol"] == "MOON"


def test_normalize_gmgn_payload_uppercases_symbol():
    candidate = normalize_gmgn_payload(
        {
            "id": "g5",
            "text": "launch",
            "author": "gmgn",
            "token_data": {"name": "Pepe", "symbol": "pepe"},
        },
        "https://gmgn.ai/base/token/g5",
    )
    assert candidate.suggested_symbol == "PEPE"


def test_normalize_gmgn_payload_no_token_data_leaves_suggested_none():
    candidate = normalize_gmgn_payload(
        {"id": "g6", "text": "launch", "author": "gmgn"},
        "https://gmgn.ai/base/token/g6",
    )
    assert candidate.suggested_name is None
    assert candidate.suggested_symbol is None


def test_normalize_gmgn_payload_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(gmgn_detector, "_utc_now_iso", lambda: "2026-04-04T01:02:03Z")

    candidate = normalize_gmgn_payload(
        {"id": "g2", "text": "launch Pepe on Base", "author": "gmgn"},
        "https://gmgn.ai/token/g2",
    )

    assert candidate.observed_at == "2026-04-04T01:02:03Z"
