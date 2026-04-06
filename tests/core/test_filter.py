from clankandclaw.core.filter import quick_filter
from clankandclaw.models.token import SignalCandidate


def build_candidate(raw_text: str) -> SignalCandidate:
    return SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )


def test_quick_filter_rejects_without_deploy_keyword():
    decision = quick_filter(build_candidate("gm"))
    assert decision.allowed is False
    assert "missing_deploy_keyword" in decision.reason_codes


def test_quick_filter_rejects_substring_only_prelaunch():
    decision = quick_filter(build_candidate("prelaunch the token"))
    assert decision.allowed is False
    assert "missing_deploy_keyword" in decision.reason_codes


def test_quick_filter_rejects_substring_only_launched():
    decision = quick_filter(build_candidate("launched the token"))
    assert decision.allowed is False
    assert "missing_deploy_keyword" in decision.reason_codes


def test_quick_filter_accepts_whole_word_launch():
    decision = quick_filter(build_candidate("launch the token"))
    assert decision.allowed is True
    assert "keyword_match" in decision.reason_codes


def test_quick_filter_x_allows_target_mention_with_contract():
    candidate = SignalCandidate(
        id="x-99",
        source="x",
        source_event_id="99",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="@bankrbot ca 0x1234567890abcdef1234567890abcdef12345678",
        fingerprint="fp-x99",
        metadata={"x_target_mention": True, "has_contract": True, "x_intent_score": 0},
    )
    decision = quick_filter(candidate)
    assert decision.allowed is True
    assert "x_target_intent" in decision.reason_codes


def test_quick_filter_farcaster_allows_target_mention_with_intent():
    candidate = SignalCandidate(
        id="fc-99",
        source="farcaster",
        source_event_id="99",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="@bankr deploy moon",
        fingerprint="fp-fc99",
        metadata={"fc_target_mention": True, "has_contract": False, "fc_intent_score": 2},
    )
    decision = quick_filter(candidate)
    assert decision.allowed is True
    assert "farcaster_target_intent" in decision.reason_codes


def test_quick_filter_gecko_base_requires_target_source_match():
    candidate = SignalCandidate(
        id="g-1",
        source="gecko",
        source_event_id="base:0x1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-g-1",
        # _evaluate_pool always sets gate_stage="stage3_failed" when base source doesn't match.
        metadata={"network": "base", "hot_score": 5, "source_match_score": 0, "gate_stage": "stage3_failed"},
    )
    decision = quick_filter(candidate)
    assert decision.allowed is False
    assert "stage3_failed" in decision.reason_codes


def test_quick_filter_gecko_base_allows_when_target_source_matches():
    candidate = SignalCandidate(
        id="g-2",
        source="gecko",
        source_event_id="base:0x2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-g-2",
        metadata={
            "network": "base",
            "hot_score": 5,
            "source_match_score": 1,
            "confidence_tier": "medium",
            "gate_stage": "stage2_passed",
        },
    )
    decision = quick_filter(candidate)
    assert decision.allowed is True
    assert "gecko_confidence_medium" in decision.reason_codes


def test_quick_filter_gecko_rejects_failed_gate_stage():
    candidate = SignalCandidate(
        id="g-3",
        source="gecko",
        source_event_id="base:0x3",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-g-3",
        metadata={
            "network": "base",
            "gate_stage": "stage2_failed",
            "hot_score": 5,
            "source_match_score": 2,
            "confidence_tier": "high",
        },
    )
    decision = quick_filter(candidate)
    assert decision.allowed is False
    assert "stage2_failed" in decision.reason_codes


def test_quick_filter_gecko_low_confidence_override_on_strong_momentum():
    candidate = SignalCandidate(
        id="g-4",
        source="gecko",
        source_event_id="solana:0x4",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-g-4",
        metadata={
            "network": "solana",
            "hot_score": 6,
            "confidence_tier": "low",
            "volume": {"m1": 1500.0, "m5": 8000.0},
            "transactions": {"m1": 5, "m5": 20},
            "spike_ratio_m1_m5": 0.4,
        },
    )
    decision = quick_filter(candidate)
    assert decision.allowed is True
    assert "gecko_low_confidence_override" in decision.reason_codes
