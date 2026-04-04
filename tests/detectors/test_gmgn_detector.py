from clankandclaw.core.detectors.gmgn_detector import normalize_gmgn_payload


def test_normalize_gmgn_payload_returns_signal_candidate():
    candidate = normalize_gmgn_payload(
        {"id": "g1", "text": "launch Pepe on Base", "author": "gmgn"},
        "https://gmgn.ai/token/g1",
    )
    assert candidate.source == "gmgn"
