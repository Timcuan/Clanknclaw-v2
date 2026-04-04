from clankandclaw.core.detectors.x_detector import normalize_x_event


def test_normalize_x_event_returns_signal_candidate():
    candidate = normalize_x_event(
        {"id": "1", "text": "deploy Pepe symbol PEPE", "user": {"username": "alice"}},
        "https://x.example/1",
    )
    assert candidate.source == "x"
