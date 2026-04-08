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


def test_score_candidate_gecko_eth_early_window_bonus_applies():
    candidate = SignalCandidate(
        id="g-eth-2",
        source="gecko",
        source_event_id="eth:0x2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="hot pool",
        fingerprint="fp-ge-2",
        metadata={
            "network": "eth",
            "scan_mode": "new_pools",
            "pool_age_minutes": 20.0,
            "volume": {"m5": 2600.0, "m15": 5500.0},
            "transactions": {"m5": 9},
            "liquidity_usd": 7000.0,
            "hot_score": 4,
            "confidence_tier": "medium",
            "buy_ratio_m5": 0.65,
        },
    )
    scored = score_candidate(candidate)
    assert "eth_early_window" in scored.reason_codes
    assert "eth_buy_pressure" in scored.reason_codes


def test_score_candidate_gecko_solana_meme_cn_narrative_bonus():
    candidate = SignalCandidate(
        id="g-sol-2",
        source="gecko",
        source_event_id="solana:0x2",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="New launch pool detected: Dragon Pepe meme coin moon mission",
        fingerprint="fp-gsol-2",
        metadata={
            "network": "solana",
            "volume": {"m5": 14000.0, "m15": 30000.0},
            "transactions": {"m5": 36},
            "liquidity_usd": 26000.0,
            "hot_score": 6,
            "confidence_tier": "high",
        },
    )
    scored = score_candidate(candidate)
    assert "solana_meme_narrative" in scored.reason_codes
    assert "solana_cn_narrative" in scored.reason_codes


def test_score_candidate_gecko_eth_meme_only_gets_narrative_penalty():
    candidate = SignalCandidate(
        id="g-eth-3",
        source="gecko",
        source_event_id="eth:0x3",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="Pepe doge meme coin moon on eth",
        fingerprint="fp-geth-3",
        metadata={
            "network": "eth",
            "scan_mode": "new_pools",
            "pool_age_minutes": 20.0,
            "volume": {"m5": 2400.0, "m15": 7000.0},
            "transactions": {"m5": 10},
            "liquidity_usd": 9000.0,
            "hot_score": 4,
            "confidence_tier": "medium",
            "buy_ratio_m5": 0.61,
        },
    )
    scored = score_candidate(candidate)
    assert "eth_meme_narrative_penalty" in scored.reason_codes


def test_score_candidate_gecko_ai_enrichment_bonus_and_penalty():
    candidate = SignalCandidate(
        id="g-base-4",
        source="gecko",
        source_event_id="base:0x4",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="AI infra launch on base",
        fingerprint="fp-gb-4",
        metadata={
            "network": "base",
            "scan_mode": "new_pools",
            "pool_age_minutes": 15.0,
            "volume": {"m5": 3200.0, "m15": 9000.0},
            "transactions": {"m5": 14},
            "liquidity_usd": 12000.0,
            "hot_score": 5,
            "confidence_tier": "high",
            "buy_ratio_m5": 0.65,
            "ai_enriched": True,
            "ai_bullish_score": 88,
            "ai_is_genuine": False,
        },
    )
    scored = score_candidate(candidate)
    assert "gecko_ai_bullish_strong" in scored.reason_codes
    assert "gecko_ai_launch_doubt" in scored.reason_codes


def test_score_gecko_base_penalises_bot_spike_pattern():
    """Base: extreme m1/m5 spike + low buy ratio → bot risk penalty."""
    candidate = SignalCandidate(
        id="g-base-bot",
        source="gecko",
        source_event_id="base:0xbot",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="base pool",
        suggested_symbol="BOT",
        fingerprint="fp-base-bot",
        metadata={
            "network": "base",
            "volume": {"m1": 8000.0, "m5": 9000.0, "m15": 20000.0},
            "transactions": {"m1": 5, "m5": 12},
            "liquidity_usd": 5000.0,
            "spike_ratio_m1_m5": 0.89,
            "buy_ratio_m5": 0.40,
            "hot_score": 3,
            "confidence_tier": "medium",
            "scan_mode": "new_pools",
            "pool_age_minutes": 8.0,
        },
    )
    scored = score_candidate(candidate)
    assert "base_bot_risk" in scored.reason_codes


def test_score_gecko_base_no_penalty_with_healthy_buy_ratio():
    """Base: high spike but healthy buy ratio → no bot risk penalty."""
    candidate = SignalCandidate(
        id="g-base-ok",
        source="gecko",
        source_event_id="base:0xok",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="base pool",
        suggested_symbol="LEGIT",
        fingerprint="fp-base-ok",
        metadata={
            "network": "base",
            "volume": {"m1": 8000.0, "m5": 9000.0, "m15": 20000.0},
            "transactions": {"m1": 5, "m5": 12},
            "liquidity_usd": 5000.0,
            "spike_ratio_m1_m5": 0.89,
            "buy_ratio_m5": 0.62,
            "hot_score": 3,
            "confidence_tier": "medium",
            "scan_mode": "new_pools",
            "pool_age_minutes": 8.0,
        },
    )
    scored = score_candidate(candidate)
    assert "base_bot_risk" not in scored.reason_codes


def test_score_gecko_eth_penalises_bot_spike_pattern():
    """ETH: extreme spike + sell pressure → bot risk penalty."""
    candidate = SignalCandidate(
        id="g-eth-bot",
        source="gecko",
        source_event_id="eth:0xbot",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="eth pool",
        suggested_symbol="DUMP",
        fingerprint="fp-eth-bot",
        metadata={
            "network": "eth",
            "volume": {"m1": 12000.0, "m5": 13000.0, "m15": 25000.0},
            "transactions": {"m1": 4, "m5": 8},
            "liquidity_usd": 8000.0,
            "spike_ratio_m1_m5": 0.92,
            "buy_ratio_m5": 0.38,
            "hot_score": 4,
            "confidence_tier": "medium",
            "scan_mode": "new_pools",
            "pool_age_minutes": 15.0,
        },
    )
    scored = score_candidate(candidate)
    assert "eth_bot_risk" in scored.reason_codes
