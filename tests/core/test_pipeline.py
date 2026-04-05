from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import SignalCandidate


def test_process_candidate_creates_priority_review(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    candidate = SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="deploy PEPE now on base",
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )
    result = process_candidate(db, candidate)
    assert result.decision == "priority_review"
