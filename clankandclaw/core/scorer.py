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
