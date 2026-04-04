from dataclasses import dataclass

from clankandclaw.models.token import SignalCandidate


@dataclass
class ScoreResult:
    score: int
    reason_codes: list[str]


def score_candidate(candidate: SignalCandidate) -> ScoreResult:
    score = 40
    reasons = ["base_score"]
    lowered = candidate.raw_text.lower()
    if "deploy" in lowered:
        score += 25
        reasons.append("deploy_keyword")
    if "base" in lowered:
        score += 20
        reasons.append("base_context")
    if candidate.suggested_symbol:
        score += 10
        reasons.append("symbol_present")
    return ScoreResult(score=score, reason_codes=reasons)
