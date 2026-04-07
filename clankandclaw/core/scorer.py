import re
from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class ScoreResult:
    score: int
    reason_codes: list[str]


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def score_candidate(candidate: SignalCandidate) -> ScoreResult:
    score = 40
    reasons = ["base_score"]
    if candidate.source == "x":
        metadata = candidate.metadata or {}
        x_target_mention = bool(metadata.get("x_target_mention"))
        has_contract = bool(metadata.get("has_contract"))
        x_intent_score = int(metadata.get("x_intent_score") or 0)
        x_engagement_score = int(metadata.get("x_engagement_score") or 0)
        lowered = candidate.raw_text.lower()

        if x_target_mention:
            score += 18
            reasons.append("x_target_mention")
        if _contains_word(lowered, "deploy") or _contains_word(lowered, "launch"):
            score += 25
            reasons.append("deploy_keyword")
        if has_contract:
            score += 18
            reasons.append("x_contract_present")
        if x_intent_score >= 3:
            score += 15
            reasons.append("x_intent_strong")
        elif x_intent_score >= 1:
            score += 8
            reasons.append("x_intent_present")

        if x_engagement_score >= 60:
            score += 12
            reasons.append("x_engagement_strong")
        elif x_engagement_score >= 15:
            score += 6
            reasons.append("x_engagement_ok")

        if _contains_word(lowered, "base"):
            score += 10
            reasons.append("base_context")
        if candidate.suggested_symbol:
            score += 8
            reasons.append("symbol_present")
            
        # --- AI Enrichment Signals ---
        if metadata.get("ai_enriched"):
            if metadata.get("ai_is_genuine"):
                score += 30
                reasons.append("ai_launch_intent_verified")
            
            bullish = int(metadata.get("ai_bullish_score") or 0)
            if bullish >= 85:
                score += 15
                reasons.append("ai_bullish_strong")
            elif bullish >= 70:
                score += 8
                reasons.append("ai_bullish_ok")

        return ScoreResult(score=score, reason_codes=reasons)

    if candidate.source == "farcaster":
        metadata = candidate.metadata or {}
        fc_target_mention = bool(metadata.get("fc_target_mention"))
        has_contract = bool(metadata.get("has_contract"))
        fc_intent_score = int(metadata.get("fc_intent_score") or 0)
        fc_engagement_score = int(metadata.get("fc_engagement_score") or 0)
        lowered = candidate.raw_text.lower()

        if fc_target_mention:
            score += 16
            reasons.append("farcaster_target_mention")
        if _contains_word(lowered, "deploy") or _contains_word(lowered, "launch"):
            score += 22
            reasons.append("deploy_keyword")
        if has_contract:
            score += 16
            reasons.append("farcaster_contract_present")
        if fc_intent_score >= 3:
            score += 12
            reasons.append("farcaster_intent_strong")
        elif fc_intent_score >= 1:
            score += 6
            reasons.append("farcaster_intent_present")

        if fc_engagement_score >= 40:
            score += 10
            reasons.append("farcaster_engagement_strong")
        elif fc_engagement_score >= 12:
            score += 5
            reasons.append("farcaster_engagement_ok")
        if candidate.suggested_symbol:
            score += 6
            reasons.append("symbol_present")

        # --- AI Enrichment Signals ---
        if metadata.get("ai_enriched"):
            if metadata.get("ai_is_genuine"):
                score += 25  # Slightly less than X to balance FC noise
                reasons.append("ai_launch_intent_verified")
            
            bullish = int(metadata.get("ai_bullish_score") or 0)
            if bullish >= 85:
                score += 12
                reasons.append("ai_bullish_strong")
            elif bullish >= 70:
                score += 6
                reasons.append("ai_bullish_ok")

        return ScoreResult(score=score, reason_codes=reasons)

    if candidate.source == "gecko":
        metadata = candidate.metadata or {}
        network = str(metadata.get("network") or "").lower()
        volume = metadata.get("volume") or {}
        tx_data = metadata.get("transactions") or {}
        liquidity = float(metadata.get("liquidity_usd") or 0.0)
        spike_ratio = float(metadata.get("spike_ratio") or 0.0)
        spike_ratio_m1_m5 = float(metadata.get("spike_ratio_m1_m5") or 0.0)
        source_match_score = int(metadata.get("source_match_score") or 0)
        confidence_tier = str(metadata.get("confidence_tier") or "low").lower()
        buy_ratio_m5 = float(metadata.get("buy_ratio_m5") or 0.0)
        scan_mode = str(metadata.get("scan_mode") or "new_pools")
        pool_age = float(metadata.get("pool_age_minutes") or 999.0)

        volume_m1 = float(volume.get("m1") or 0.0)
        volume_m5 = float(volume.get("m5") or 0.0)
        volume_m15 = float(volume.get("m15") or 0.0)
        tx_m1 = int(tx_data.get("m1") or 0)
        tx_m5 = int(tx_data.get("m5") or 0)
        hot_score = int(metadata.get("hot_score") or 0)

        # --- Volume M5 (tuned for Base early launches) ---
        if volume_m5 >= 15000:
            score += 25
            reasons.append("gecko_volume_m5_strong")
        elif volume_m5 >= 4000:
            score += 15
            reasons.append("gecko_volume_m5_ok")
        elif volume_m5 >= 800:
            score += 8
            reasons.append("gecko_volume_m5_light")

        # --- Volume M1 (recent burst signal) ---
        if volume_m1 >= 5000:
            score += 12
            reasons.append("gecko_volume_m1_strong")
        elif volume_m1 >= 800:
            score += 6
            reasons.append("gecko_volume_m1_ok")

        # --- Volume M15 (sustained momentum) ---
        if volume_m15 >= 30000:
            score += 18
            reasons.append("gecko_volume_m15_strong")
        elif volume_m15 >= 8000:
            score += 10
            reasons.append("gecko_volume_m15_ok")
        elif volume_m15 >= 1500:
            score += 5
            reasons.append("gecko_volume_m15_light")

        # --- TX Count M1 (immediate activity) ---
        if tx_m1 >= 10:
            score += 10
            reasons.append("gecko_tx_m1_strong")
        elif tx_m1 >= 3:
            score += 5
            reasons.append("gecko_tx_m1_ok")

        # --- TX Count M5 (sustained activity) ---
        if tx_m5 >= 40:
            score += 15
            reasons.append("gecko_tx_m5_strong")
        elif tx_m5 >= 12:
            score += 8
            reasons.append("gecko_tx_m5_ok")
        elif tx_m5 >= 3:
            score += 3
            reasons.append("gecko_tx_m5_light")

        # --- Liquidity (realistic Base thresholds) ---
        if liquidity >= 50000:
            score += 12
            reasons.append("gecko_liquidity_strong")
        elif liquidity >= 8000:
            score += 7
            reasons.append("gecko_liquidity_ok")
        elif liquidity >= 1000:
            score += 3
            reasons.append("gecko_liquidity_light")

        # --- Network bonus ---
        if network in {"base", "eth", "solana", "bsc"}:
            score += 5
            reasons.append(f"network_{network}")

        # --- Spike Ratio (momentum velocity) ---
        if spike_ratio >= 0.65:
            score += 12
            reasons.append("gecko_spike_ratio_strong")
        elif spike_ratio >= 0.35:
            score += 6
            reasons.append("gecko_spike_ratio_ok")

        # --- M1/M5 Spike Ratio (very recent burst) ---
        if spike_ratio_m1_m5 >= 0.75:
            score += 10
            reasons.append("gecko_spike_m1_m5_strong")
        elif 0.3 <= spike_ratio_m1_m5 < 0.75:
            # Healthy recent burst (not extreme = not bot pump)
            score += 8
            reasons.append("gecko_spike_m1_m5_healthy")
        elif spike_ratio_m1_m5 >= 0.2:
            score += 3
            reasons.append("gecko_spike_m1_m5_ok")

        # --- Buy Pressure (most genuine demand signal) ---
        if buy_ratio_m5 >= 0.70 and tx_m5 >= 3:
            score += 14
            reasons.append("gecko_buy_pressure_strong")
        elif buy_ratio_m5 >= 0.55 and tx_m5 >= 3:
            score += 8
            reasons.append("gecko_buy_pressure_ok")

        # --- Confidence Tier (reduced inflation: was +15, now +8) ---
        if confidence_tier == "high":
            score += 8
            reasons.append("gecko_confidence_high")
        elif confidence_tier == "medium":
            score += 4
            reasons.append("gecko_confidence_medium")
        elif confidence_tier == "low":
            score -= 10
            reasons.append("gecko_confidence_low")

        # --- New Launch Bonus (age-weighted, only for new_pools mode) ---
        if scan_mode == "new_pools":
            if pool_age <= 10:
                score += 15
                reasons.append("gecko_ultra_fresh")
            elif pool_age <= 25:
                score += 10
                reasons.append("gecko_very_fresh")
            elif pool_age <= 60:
                score += 5
                reasons.append("gecko_fresh")

        # --- Trending Mode Bonus ---
        if scan_mode == "trending_pools":
            score += 5
            reasons.append("gecko_trending_signal")

        # --- Source Match Bonus (Base DEX: Clanker, Bankr, Doppler, etc.) ---
        if network == "base" and source_match_score >= 1:
            score += 10
            reasons.append("base_target_source")

        # --- Multi-chain Attention Burst ---
        if network in {"solana", "bsc"} and tx_m5 >= 30 and volume_m5 >= 8000:
            score += 10
            reasons.append(f"{network}_attention_burst")

        # --- Hot Gate bonus ---
        if hot_score >= 5:
            score += 8
            reasons.append("gecko_hot_gate")
        elif hot_score >= 3:
            score += 4
            reasons.append("gecko_hot_gate_ok")

        # --- Symbol present (deployable token) ---
        if candidate.suggested_symbol:
            score += 5
            reasons.append("symbol_present")

        return ScoreResult(score=score, reason_codes=reasons)

    lowered = candidate.raw_text.lower()
    if _contains_word(lowered, "deploy") or _contains_word(lowered, "launch"):
        score += 25
        reasons.append("deploy_keyword")
    if _contains_word(lowered, "base"):
        score += 20
        reasons.append("base_context")
    if candidate.suggested_symbol:
        score += 10
        reasons.append("symbol_present")
    return ScoreResult(score=score, reason_codes=reasons)
