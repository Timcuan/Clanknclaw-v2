from clankandclaw.core.scorer import score_candidate
from clankandclaw.models.token import SignalCandidate


def build_candidate(raw_text: str) -> SignalCandidate:
    return SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )


def test_score_candidate_marks_strong_signal_high():
    scored = score_candidate(build_candidate("deploy PEPE now on base"))
    assert scored.score >= 80


def test_score_candidate_treats_launch_like_deploy():
    scored = score_candidate(build_candidate("launch PEPE on base"))
    assert scored.score >= 80


def test_score_candidate_does_not_credit_base_substrings():
    scored = score_candidate(build_candidate("based coinbase baseball"))
    assert "base_context" not in scored.reason_codes
