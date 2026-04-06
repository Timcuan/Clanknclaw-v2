from clankandclaw.core.scorer import score_candidate
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


def test_score_candidate_marks_strong_signal_high():
    scored = score_candidate(build_candidate("deploy PEPE now on base"))
    assert scored.score >= 80


def test_score_candidate_treats_launch_like_deploy():
    scored = score_candidate(build_candidate("launch PEPE on base"))
    assert scored.score >= 80


def test_score_candidate_does_not_credit_base_substrings():
    scored = score_candidate(build_candidate("based coinbase baseball"))
    assert "base_context" not in scored.reason_codes


def test_score_candidate_x_target_mention_contract_and_engagement():
    candidate = SignalCandidate(
        id="x-2",
        source="x",
        source_event_id="2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="@bankrbot deploy $MOON ca 0x1234567890abcdef1234567890abcdef12345678 on base",
        suggested_symbol="MOON",
        fingerprint="fp-x2",
        metadata={
            "x_target_mention": True,
            "has_contract": True,
            "x_intent_score": 3,
            "x_engagement_score": 70,
        },
    )
    scored = score_candidate(candidate)
    assert scored.score >= 110
    assert "x_target_mention" in scored.reason_codes
    assert "x_contract_present" in scored.reason_codes
    assert "x_engagement_strong" in scored.reason_codes


def test_score_candidate_farcaster_target_mention_contract_and_engagement():
    candidate = SignalCandidate(
        id="fc-2",
        source="farcaster",
        source_event_id="2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="@clanker launch $CLNK ca 0x1234567890abcdef1234567890abcdef12345678",
        suggested_symbol="CLNK",
        fingerprint="fp-fc2",
        metadata={
            "fc_target_mention": True,
            "has_contract": True,
            "fc_intent_score": 3,
            "fc_engagement_score": 45,
        },
    )
    scored = score_candidate(candidate)
    assert scored.score >= 100
    assert "farcaster_target_mention" in scored.reason_codes
    assert "farcaster_contract_present" in scored.reason_codes


def test_score_candidate_gecko_base_boosts_target_source():
    candidate = SignalCandidate(
        id="g-base-1",
        source="gecko",
        source_event_id="base:0x1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        suggested_symbol="MOON",
        fingerprint="fp-gb-1",
        metadata={
            "network": "base",
            "volume": {"m5": 18000.0, "m15": 40000.0},
            "transactions": {"m5": 46},
            "liquidity_usd": 120000.0,
            "hot_score": 5,
            "spike_ratio": 0.72,
            "source_match_score": 2,
        },
    )
    scored = score_candidate(candidate)
    assert scored.score >= 120
    assert "base_target_source" in scored.reason_codes


def test_score_candidate_gecko_solana_attention_burst_reason():
    candidate = SignalCandidate(
        id="g-sol-1",
        source="gecko",
        source_event_id="solana:0x1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        suggested_symbol="SOLM",
        fingerprint="fp-gs-1",
        metadata={
            "network": "solana",
            "volume": {"m5": 12000.0, "m15": 26000.0},
            "transactions": {"m5": 38},
            "liquidity_usd": 25000.0,
            "hot_score": 5,
            "spike_ratio": 0.55,
        },
    )
    scored = score_candidate(candidate)
    assert "solana_attention_burst" in scored.reason_codes


def test_score_candidate_gecko_confidence_high_and_m1_momentum():
    candidate = SignalCandidate(
        id="g-base-2",
        source="gecko",
        source_event_id="base:0x2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-gb-2",
        metadata={
            "network": "base",
            "volume": {"m1": 9000.0, "m5": 17000.0, "m15": 38000.0},
            "transactions": {"m1": 15, "m5": 48},
            "liquidity_usd": 110000.0,
            "hot_score": 7,
            "spike_ratio": 0.7,
            "spike_ratio_m1_m5": 0.9,
            "source_match_score": 2,
            "confidence_tier": "high",
            "gate_stage": "stage2_passed",
        },
    )
    scored = score_candidate(candidate)
    assert "gecko_confidence_high" in scored.reason_codes
    assert "gecko_volume_m1_strong" in scored.reason_codes
    assert "gecko_tx_m1_strong" in scored.reason_codes
    assert "gecko_spike_m1_m5_strong" in scored.reason_codes


def test_score_candidate_gecko_low_confidence_penalized():
    # filter.py blocks gate-failed candidates before scorer is called.
    # A low-confidence, low-signal base pool that passes gates scores below threshold.
    candidate = SignalCandidate(
        id="g-base-3",
        source="gecko",
        source_event_id="base:0x3",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-gb-3",
        metadata={
            "network": "base",
            "volume": {"m1": 100.0, "m5": 1200.0, "m15": 2500.0},
            "transactions": {"m1": 1, "m5": 5},
            "liquidity_usd": 9000.0,
            "hot_score": 2,
            "confidence_tier": "low",
            "gate_stage": "stage2_passed",
        },
    )
    scored = score_candidate(candidate)
    assert "gecko_confidence_low" in scored.reason_codes
    assert scored.score < 60
