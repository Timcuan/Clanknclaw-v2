from clankandclaw.core.filter import quick_filter
from clankandclaw.core.router import route_candidate
from clankandclaw.core.scorer import score_candidate
from clankandclaw.models.token import ScoredCandidate, SignalCandidate
import logging

logger = logging.getLogger(__name__)


def should_perform_ai_enrichment(candidate: SignalCandidate) -> bool:
    """
    Tiered Gatekeeper: Decide if a candidate warrants expensive LLM analysis.
    
    Tier 1 (Always): Verified Contract found.
    Tier 2 (High Proof): Intent > 5 OR Significant Social Engagement (>5 likes).
    Tier 3 (Skip): Noise/Spam.
    """
    meta = candidate.metadata or {}
    
    # Tier 1: Verified Contract (Hard Alpha)
    if meta.get("has_contract", False) or meta.get("evm_contracts"):
        return True
        
    # Tier 2: Strong Human Intent & Social Proof
    # intent_score is calculated during normalize_x|fc_event
    intent = meta.get("x_intent_score") or meta.get("fc_intent_score") or 0
    likes = meta.get("like_count") or meta.get("reaction_count") or 0
    replies = meta.get("reply_count") or 0
    
    if intent >= 8:
        return True # Extremely high intent keyword match
        
    if intent >= 4 and (likes >= 5 or replies >= 3):
        return True # Moderate intent with social validation
        
    logger.debug(f"AI Enrichment Skipped for {candidate.id} (Heuristic Gatekeeper: intent={intent}, likes={likes})")
    return False


def process_candidate(db, candidate: SignalCandidate) -> ScoredCandidate:
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
        db.save_candidate_and_decision(
            candidate_id=candidate.id,
            source=candidate.source,
            source_event_id=candidate.source_event_id,
            fingerprint=candidate.fingerprint,
            raw_text=candidate.raw_text,
            score=scored.score,
            decision=scored.decision,
            reason_codes=scored.reason_codes,
            recommended_platform=scored.recommended_platform,
            observed_at=candidate.observed_at,
            metadata=candidate.metadata,
        )
        return scored
    score = score_candidate(candidate)
    route = route_candidate(score.score)
    scored = ScoredCandidate(
        candidate_id=candidate.id,
        score=score.score,
        decision=route.decision,
        reason_codes=score.reason_codes,
        recommended_platform=route.recommended_platform,
        review_priority=route.review_priority,
        auto_trigger=route.auto_trigger,
    )
    db.save_candidate_and_decision(
        candidate_id=candidate.id,
        source=candidate.source,
        source_event_id=candidate.source_event_id,
        fingerprint=candidate.fingerprint,
        raw_text=candidate.raw_text,
        score=scored.score,
        decision=scored.decision,
        reason_codes=scored.reason_codes,
        recommended_platform=scored.recommended_platform,
        review_priority=scored.review_priority,
        auto_trigger=scored.auto_trigger,
        observed_at=candidate.observed_at,
        metadata=candidate.metadata,
    )
    return scored
