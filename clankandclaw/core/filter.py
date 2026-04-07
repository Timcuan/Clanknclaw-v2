import re
from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class FilterDecision:
    allowed: bool
    reason_codes: list[str]


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def quick_filter(candidate: SignalCandidate) -> FilterDecision:
    if candidate.source == "gecko":
        metadata = candidate.metadata or {}
        network = str(metadata.get("network") or "").lower()
        source_match_score = int(metadata.get("source_match_score") or 0)
        gate_stage = str(metadata.get("gate_stage") or "")
        confidence_tier = str(metadata.get("confidence_tier") or "low").lower()
        volume = metadata.get("volume") or {}
        tx_data = metadata.get("transactions") or {}
        volume_m1 = float(volume.get("m1") or 0.0)
        volume_m5 = float(volume.get("m5") or 0.0)
        tx_m1 = int(tx_data.get("m1") or 0)
        tx_m5 = int(tx_data.get("m5") or 0)
        hot_score = int(metadata.get("hot_score") or 0)
        spike_ratio_m1_m5 = float(metadata.get("spike_ratio_m1_m5") or 0.0)
        buy_ratio_m5 = float(metadata.get("buy_ratio_m5") or 0.0)

        # Hard reject: gate failed at detector level
        if gate_stage in {"stage1_failed", "stage2_failed", "stage3_failed"}:
            return FilterDecision(False, [gate_stage])

        # Hard reject: zero activity — only when activity fields are explicitly present.
        has_volume_data = isinstance(volume, dict) and any(key in volume for key in ("m1", "m5", "m15"))
        has_tx_data = isinstance(tx_data, dict) and any(key in tx_data for key in ("m1", "m5"))
        if (has_volume_data or has_tx_data) and volume_m5 < 50 and tx_m5 < 2:
            return FilterDecision(False, ["gecko_zero_activity"])

        # Hard reject: extreme sell pressure — dump signal
        if buy_ratio_m5 > 0 and buy_ratio_m5 < 0.25 and tx_m5 >= 5:
            return FilterDecision(False, ["gecko_sell_dominated"])

        strong_momentum = (
            hot_score >= 5
            and volume_m5 >= 2000
            and tx_m5 >= 10
            and (spike_ratio_m1_m5 >= 0.2 or (volume_m1 >= 300 and tx_m1 >= 2))
        )
        if confidence_tier in {"high", "medium"}:
            return FilterDecision(True, [f"gecko_confidence_{confidence_tier}"])
        if strong_momentum:
            return FilterDecision(True, ["gecko_low_confidence_override"])
        return FilterDecision(False, ["gecko_not_hot"])

    if candidate.source == "x":
        metadata = candidate.metadata or {}
        x_target_mention = bool(metadata.get("x_target_mention"))
        x_intent_score = int(metadata.get("x_intent_score") or 0)
        has_contract = bool(metadata.get("has_contract"))
        if x_target_mention and (has_contract or x_intent_score >= 1):
            return FilterDecision(True, ["x_target_intent"])

    if candidate.source == "farcaster":
        metadata = candidate.metadata or {}
        fc_target_mention = bool(metadata.get("fc_target_mention"))
        fc_intent_score = int(metadata.get("fc_intent_score") or 0)
        has_contract = bool(metadata.get("has_contract"))
        if fc_target_mention and (has_contract or fc_intent_score >= 1):
            return FilterDecision(True, ["farcaster_target_intent"])

    lowered = candidate.raw_text.lower()
    if not _contains_word(lowered, "deploy") and not _contains_word(lowered, "launch"):
        return FilterDecision(False, ["missing_deploy_keyword"])
    return FilterDecision(True, ["keyword_match"])
