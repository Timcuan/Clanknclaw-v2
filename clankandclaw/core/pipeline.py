from clankandclaw.core.filter import quick_filter
from clankandclaw.core.router import route_candidate
from clankandclaw.core.scorer import score_candidate
from clankandclaw.models.token import ScoredCandidate, SignalCandidate


def process_candidate(db, candidate: SignalCandidate) -> ScoredCandidate:
    db.save_candidate(candidate.id, candidate.source, candidate.source_event_id, candidate.fingerprint, candidate.raw_text)
    filter_decision = quick_filter(candidate)
    if not filter_decision.allowed:
        scored = ScoredCandidate(
            candidate_id=candidate.id,
            score=0,
            decision="skip",
            reason_codes=filter_decision.reason_codes,
            recommended_platform="clanker",
            review_priority="review",
        )
        db.save_decision(candidate.id, scored.score, scored.decision, scored.reason_codes, scored.recommended_platform)
        return scored
    score = score_candidate(candidate)
    route = route_candidate(score.score)
    scored = ScoredCandidate(
        candidate_id=candidate.id,
        score=score.score,
        decision=route.decision,
        reason_codes=score.reason_codes,
        recommended_platform="clanker",
        review_priority=route.review_priority,
    )
    db.save_decision(candidate.id, scored.score, scored.decision, scored.reason_codes, scored.recommended_platform)
    return scored
