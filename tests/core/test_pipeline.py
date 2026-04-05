import sqlite3

from clankandclaw.core.pipeline import process_candidate
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import SignalCandidate


def _build_candidate(
    candidate_id: str,
    *,
    raw_text: str,
    suggested_symbol: str | None = "PEPE",
) -> SignalCandidate:
    return SignalCandidate(
        id=candidate_id,
        source="x",
        source_event_id=f"tweet-{candidate_id}",
        observed_at="2026-04-04T00:00:00Z",
        raw_text=raw_text,
        author_handle="alice",
        context_url=f"https://x.example/{candidate_id}",
        suggested_name="Pepe",
        suggested_symbol=suggested_symbol,
        fingerprint=f"fp-{candidate_id}",
        metadata={},
    )


def test_process_candidate_persists_candidate_and_decision_on_happy_path(tmp_path):
    db_path = tmp_path / "state.db"
    db = DatabaseManager(db_path)
    db.initialize()
    candidate = _build_candidate("sig-1", raw_text="deploy PEPE now on base")

    result = process_candidate(db, candidate)

    assert result.decision == "priority_review"

    with sqlite3.connect(db_path) as conn:
        candidate_row = conn.execute(
            "SELECT id, source, source_event_id, fingerprint, raw_text FROM signal_candidates WHERE id = ?",
            (candidate.id,),
        ).fetchone()
        decision_row = conn.execute(
            """
            SELECT candidate_id, score, decision, reason_codes, recommended_platform
            FROM candidate_decisions
            WHERE candidate_id = ?
            """,
            (candidate.id,),
        ).fetchone()

    assert candidate_row == (
        candidate.id,
        candidate.source,
        candidate.source_event_id,
        candidate.fingerprint,
        candidate.raw_text,
    )
    assert decision_row == (
        candidate.id,
        result.score,
        "priority_review",
        "base_score,deploy_keyword,base_context,symbol_present",
        "clanker",
    )


def test_process_candidate_persists_skip_decision(tmp_path):
    db_path = tmp_path / "state.db"
    db = DatabaseManager(db_path)
    db.initialize()
    candidate = _build_candidate("sig-skip", raw_text="watch this token", suggested_symbol=None)

    result = process_candidate(db, candidate)

    assert result.decision == "skip"

    with sqlite3.connect(db_path) as conn:
        decision_row = conn.execute(
            "SELECT candidate_id, score, decision, reason_codes, recommended_platform FROM candidate_decisions WHERE candidate_id = ?",
            (candidate.id,),
        ).fetchone()

    assert decision_row == (
        candidate.id,
        0,
        "skip",
        "missing_deploy_keyword",
        "clanker",
    )


def test_process_candidate_can_reprocess_existing_candidate_without_duplicate_failure(tmp_path):
    db_path = tmp_path / "state.db"
    db = DatabaseManager(db_path)
    db.initialize()
    candidate = _build_candidate("sig-1", raw_text="deploy PEPE now on base")

    first = process_candidate(db, candidate)
    second = process_candidate(db, candidate)

    assert second == first

    with sqlite3.connect(db_path) as conn:
        candidate_count = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE id = ?", (candidate.id,)).fetchone()[0]
        decision_count = conn.execute("SELECT COUNT(*) FROM candidate_decisions WHERE candidate_id = ?", (candidate.id,)).fetchone()[0]

    assert candidate_count == 1
    assert decision_count == 1
