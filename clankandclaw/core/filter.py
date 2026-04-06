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

        if gate_stage in {"stage1_failed", "stage2_failed", "stage3_failed"}:
            return FilterDecision(False, [gate_stage])
        if network == "base" and source_match_score < 1:
            return FilterDecision(False, ["gecko_base_source_not_target"])

        strong_momentum = (
            hot_score >= 5
            and volume_m5 >= 3500
            and tx_m5 >= 12
            and (spike_ratio_m1_m5 >= 0.2 or (volume_m1 >= 500 and tx_m1 >= 3))
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
