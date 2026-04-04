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
    lowered = candidate.raw_text.lower()
    if not _contains_word(lowered, "deploy") and not _contains_word(lowered, "launch"):
        return FilterDecision(False, ["missing_deploy_keyword"])
    return FilterDecision(True, ["keyword_match"])
