from clankandclaw.core.detectors import gecko_detector
from clankandclaw.core.detectors.gecko_detector import normalize_gecko_payload


def test_normalize_gecko_payload_uses_source_timestamp_and_normalizes_fields():
    candidate = normalize_gecko_payload(
        {
            "id": "g1",
            "text": "launch Pepe on Base",
            "timestamp": "2026-04-04T12:34:56Z",
            "author": "geckoterminal",
        },
        "https://www.geckoterminal.com/base/pools/g1",
    )

    assert candidate.source == "gecko"
    assert candidate.source_event_id == "g1"
    assert candidate.observed_at == "2026-04-04T12:34:56Z"
    assert candidate.context_url == "https://www.geckoterminal.com/base/pools/g1"
    assert candidate.author_handle == "geckoterminal"
    assert candidate.metadata["collector_mode"] == "direct_geckoterminal"
    assert candidate.metadata["context_url"] == "https://www.geckoterminal.com/base/pools/g1"
    assert candidate.metadata["author_handle"] == "geckoterminal"
    assert len(candidate.fingerprint) == 64
    int(candidate.fingerprint, 16)


def test_normalize_gecko_payload_extracts_image_url_from_token_data():
    candidate = normalize_gecko_payload(
        {
            "id": "g3",
            "text": "launch Moon on Base",
            "author": "geckoterminal",
            "token_data": {"image_url": "https://example.com/logo.png"},
        },
        "https://www.geckoterminal.com/base/pools/g3",
    )
    assert candidate.metadata["image_url"] == "https://example.com/logo.png"


def test_normalize_gecko_payload_extracts_suggested_name_symbol_from_token_data():
    candidate = normalize_gecko_payload(
        {
            "id": "g4",
            "text": "New token launch on Base chain: Moon (MOON)",
            "author": "geckoterminal",
            "token_data": {"name": "Moon", "symbol": "MOON"},
        },
        "https://www.geckoterminal.com/base/pools/g4",
    )
    assert candidate.suggested_name == "Moon"
    assert candidate.suggested_symbol == "MOON"
    assert candidate.metadata["suggested_name"] == "Moon"
    assert candidate.metadata["suggested_symbol"] == "MOON"


def test_normalize_gecko_payload_uppercases_symbol():
    candidate = normalize_gecko_payload(
        {
            "id": "g5",
            "text": "launch",
            "author": "geckoterminal",
            "token_data": {"name": "Pepe", "symbol": "pepe"},
        },
        "https://www.geckoterminal.com/base/pools/g5",
    )
    assert candidate.suggested_symbol == "PEPE"


def test_normalize_gecko_payload_no_token_data_leaves_suggested_none():
    candidate = normalize_gecko_payload(
        {"id": "g6", "text": "launch", "author": "geckoterminal"},
        "https://www.geckoterminal.com/base/pools/g6",
    )
    assert candidate.suggested_name is None
    assert candidate.suggested_symbol is None


def test_normalize_gecko_payload_falls_back_to_current_utc_time(monkeypatch):
    monkeypatch.setattr(gecko_detector, "_utc_now_iso", lambda: "2026-04-04T01:02:03Z")

    candidate = normalize_gecko_payload(
        {"id": "g2", "text": "launch Pepe on Base", "author": "geckoterminal"},
        "https://www.geckoterminal.com/base/pools/g2",
    )

    assert candidate.observed_at == "2026-04-04T01:02:03Z"
