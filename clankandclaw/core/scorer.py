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
        return ScoreResult(score=score, reason_codes=reasons)

    if candidate.source == "gecko":
        metadata = candidate.metadata or {}
        network = str(metadata.get("network") or "").lower()
        volume = metadata.get("volume") or {}
        tx_data = metadata.get("transactions") or {}
        liquidity = float(metadata.get("liquidity_usd") or 0.0)
        spike_ratio = float(metadata.get("spike_ratio") or 0.0)
        source_match_score = int(metadata.get("source_match_score") or 0)

        volume_m5 = float(volume.get("m5") or 0.0)
        volume_m15 = float(volume.get("m15") or 0.0)
        tx_m5 = int(tx_data.get("m5") or 0)
        hot_score = int(metadata.get("hot_score") or 0)

        if volume_m5 >= 15000:
            score += 25
            reasons.append("gecko_volume_m5_strong")
        elif volume_m5 >= 6000:
            score += 15
            reasons.append("gecko_volume_m5_ok")

        if volume_m15 >= 35000:
            score += 20
            reasons.append("gecko_volume_m15_strong")
        elif volume_m15 >= 12000:
            score += 10
            reasons.append("gecko_volume_m15_ok")

        if tx_m5 >= 45:
            score += 15
            reasons.append("gecko_tx_m5_strong")
        elif tx_m5 >= 15:
            score += 8
            reasons.append("gecko_tx_m5_ok")

        if liquidity >= 100000:
            score += 10
            reasons.append("gecko_liquidity_strong")
        elif liquidity >= 20000:
            score += 5
            reasons.append("gecko_liquidity_ok")

        if network in {"base", "eth", "solana", "bsc"}:
            score += 5
            reasons.append(f"network_{network}")

        if spike_ratio >= 0.65:
            score += 12
            reasons.append("gecko_spike_ratio_strong")
        elif spike_ratio >= 0.45:
            score += 6
            reasons.append("gecko_spike_ratio_ok")

        if network == "base" and source_match_score >= 1:
            score += 10
            reasons.append("base_target_source")

        if network in {"solana", "bsc"} and tx_m5 >= 35 and volume_m5 >= 10000:
            score += 10
            reasons.append(f"{network}_attention_burst")

        if hot_score >= 4:
            score += 5
            reasons.append("gecko_hot_gate")

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
