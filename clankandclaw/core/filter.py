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
        if network == "base" and source_match_score < 1:
            return FilterDecision(False, ["gecko_base_source_not_target"])
        hot_score = int(metadata.get("hot_score") or 0)
        if hot_score >= 4:
            return FilterDecision(True, ["gecko_hot_pool"])
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
