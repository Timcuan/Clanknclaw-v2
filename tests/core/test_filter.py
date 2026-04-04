from clankandclaw.core.filter import quick_filter
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


def test_quick_filter_rejects_without_deploy_keyword():
    decision = quick_filter(build_candidate("gm"))
    assert decision.allowed is False
    assert "missing_deploy_keyword" in decision.reason_codes
