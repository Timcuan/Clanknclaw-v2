import sqlite3

import pytest

from clankandclaw.database.manager import DatabaseManager


def test_database_manager_initializes_schema(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    tables = db.list_tables()
    assert "signal_candidates" in tables
    assert "review_items" in tables
    assert "runtime_settings" in tables


def test_database_manager_persists_candidate_and_decision(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    db.save_decision("sig-1", 85, "priority_review", ["keyword_match"], "clanker")
    row = db.get_candidate_decision("sig-1")
    assert row["decision"] == "priority_review"


def test_database_manager_persists_candidate_and_decision_atomically(tmp_path):
    db_path = tmp_path / "state.db"
    db = DatabaseManager(db_path)
    db.initialize()

    db.save_candidate_and_decision(
        candidate_id="sig-1",
        source="x",
        source_event_id="tweet-1",
        fingerprint="fp-1",
        raw_text="deploy pepe",
        score=85,
        decision="priority_review",
        reason_codes=["keyword_match"],
        recommended_platform="clanker",
    )

    with sqlite3.connect(db_path) as conn:
        candidate_row = conn.execute(
            "SELECT id, source, source_event_id, fingerprint, raw_text FROM signal_candidates WHERE id = ?",
            ("sig-1",),
        ).fetchone()
        decision_row = conn.execute(
            """
            SELECT candidate_id, score, decision, reason_codes, recommended_platform
            FROM candidate_decisions
            WHERE candidate_id = ?
            """,
            ("sig-1",),
        ).fetchone()

    assert candidate_row == ("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    assert decision_row == ("sig-1", 85, "priority_review", "keyword_match", "clanker")


def test_database_manager_save_candidate_and_decision_is_idempotent(tmp_path):
    db_path = tmp_path / "state.db"
    db = DatabaseManager(db_path)
    db.initialize()

    db.save_candidate_and_decision(
        candidate_id="sig-1",
        source="x",
        source_event_id="tweet-1",
        fingerprint="fp-1",
        raw_text="deploy pepe",
        score=85,
        decision="priority_review",
        reason_codes=["keyword_match"],
        recommended_platform="clanker",
    )
    db.save_candidate_and_decision(
        candidate_id="sig-1",
        source="x",
        source_event_id="tweet-1",
        fingerprint="fp-1",
        raw_text="deploy pepe",
        score=85,
        decision="priority_review",
        reason_codes=["keyword_match"],
        recommended_platform="clanker",
    )

    with sqlite3.connect(db_path) as conn:
        candidate_count = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE id = ?", ("sig-1",)).fetchone()[0]
        decision_count = conn.execute("SELECT COUNT(*) FROM candidate_decisions WHERE candidate_id = ?", ("sig-1",)).fetchone()[0]

    assert candidate_count == 1
    assert decision_count == 1


def test_database_manager_returns_none_when_decision_missing(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()

    assert db.get_candidate_decision("missing") is None


def test_database_manager_enforces_foreign_keys(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()

    with pytest.raises(sqlite3.IntegrityError):
        db.save_decision("missing", 85, "priority_review", ["keyword_match"], "clanker")


def test_database_manager_upgrades_legacy_review_items_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE signal_candidates (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                raw_text TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO signal_candidates (id, source, source_event_id, fingerprint, raw_text)
            VALUES ('sig-1', 'x', 'tweet-1', 'fp-1', 'deploy pepe');
            """
        )
        conn.execute(
            """
            CREATE TABLE review_items (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO review_items (id, candidate_id, status, expires_at)
            VALUES ('review-1', 'sig-1', 'pending', '2099-01-01T00:00:00Z');
            """
        )

    db = DatabaseManager(db_path)
    monkeypatch.setattr("clankandclaw.database.manager._utc_now_iso", lambda: "2026-04-05T00:00:00Z")
    db.initialize()

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(review_items)").fetchall()]
        foreign_keys = conn.execute("PRAGMA foreign_key_list(review_items)").fetchall()

    assert columns == [
        "id",
        "candidate_id",
        "status",
        "created_at",
        "expires_at",
        "locked_by",
        "locked_at",
        "telegram_message_id",
    ]
    assert any(row[2] == "signal_candidates" and row[3] == "candidate_id" and row[4] == "id" for row in foreign_keys)
    row = db.get_review_item("review-1")
    assert row is not None
    assert row["candidate_id"] == "sig-1"
    assert row["status"] == "pending"
    assert row["created_at"] == "2026-04-05T00:00:00Z"
    assert row["expires_at"] == "2099-01-01T00:00:00Z"
    assert row["locked_by"] is None
    assert row["locked_at"] is None

    with pytest.raises(sqlite3.IntegrityError):
        db.create_review_item("review-2", "missing", "2099-01-01T00:00:00Z")


def test_database_manager_fails_cleanly_when_legacy_review_items_has_orphans(tmp_path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE review_items (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO review_items (id, candidate_id, status, expires_at)
            VALUES ('review-1', 'missing-sig', 'pending', '2099-01-01T00:00:00Z');
            """
        )

    db = DatabaseManager(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        db.initialize()

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        columns = [row[1] for row in conn.execute("PRAGMA table_info(review_items)").fetchall()]

    assert tables == {"review_items"}
    assert "review_items_legacy" not in tables
    assert "signal_candidates" not in tables
    assert "candidate_decisions" not in tables
    assert columns == ["id", "candidate_id", "status", "expires_at"]


def test_get_latest_deployment_for_candidate_returns_most_recent(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    db.save_deployment_result(
        result_id="dep-1",
        candidate_id="sig-1",
        status="deploy_failed",
        error_code="err",
        error_message="first",
        deployed_at="2026-04-05T10:00:00Z",
    )
    db.save_deployment_result(
        result_id="dep-2",
        candidate_id="sig-1",
        status="deploy_success",
        tx_hash="0x" + "a" * 64,
        contract_address="0x" + "b" * 40,
        deployed_at="2026-04-05T11:00:00Z",
    )

    row = db.get_latest_deployment_for_candidate("sig-1")
    assert row is not None
    assert row["id"] == "dep-2"
    assert row["status"] == "deploy_success"


def test_save_reward_claim_result_persists_row(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()

    db.save_reward_claim_result(
        result_id="claim-1",
        token_address="0x" + "a" * 40,
        status="claim_success",
        tx_hash="0x" + "b" * 64,
        error_code=None,
        error_message=None,
        claimed_at="2026-04-05T11:00:00Z",
    )

    with sqlite3.connect(tmp_path / "state.db") as conn:
        row = conn.execute(
            "SELECT id, token_address, status, tx_hash FROM reward_claim_results WHERE id = ?",
            ("claim-1",),
        ).fetchone()

    assert row == ("claim-1", "0x" + "a" * 40, "claim_success", "0x" + "b" * 64)


def test_runtime_settings_persist_and_update(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()

    db.set_runtime_setting("telegram.thread.ops", "201")
    assert db.get_runtime_setting("telegram.thread.ops") == "201"

    db.set_runtime_setting("telegram.thread.ops", "202")
    assert db.get_runtime_setting("telegram.thread.ops") == "202"
    assert db.get_runtime_setting("telegram.thread.missing") is None


def test_runtime_settings_delete(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.set_runtime_setting("wallet.token_admin", "0x" + "1" * 40)
    assert db.get_runtime_setting("wallet.token_admin") == "0x" + "1" * 40
    db.delete_runtime_setting("wallet.token_admin")
    assert db.get_runtime_setting("wallet.token_admin") is None


def test_complete_review_item_transitions_from_deploying(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    db.create_review_item("review-1", "sig-1", "2099-01-01T00:00:00Z")
    assert db.lock_review_item("review-1", "tester") is True
    assert db.complete_review_item("review-1", success=True, locked_by="tester") is True
    row = db.get_review_item("review-1")
    assert row is not None
    assert row["status"] == "approved"


def test_database_manager_compacts_oversized_raw_text_and_metadata(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    raw_text = "x" * 5000
    metadata = {
        "context_url": "https://x.com/a/status/1",
        "author_handle": "alice",
        "raw_event": {"huge": "y" * 10000},
        "image_candidates": [f"https://example.com/{i}.png" for i in range(40)],
        "notes": "z" * 2000,
    }
    db.save_candidate(
        "sig-big",
        "x",
        "tweet-big",
        "fp-big",
        raw_text,
        metadata=metadata,
    )
    row = db.get_candidate("sig-big")
    assert row is not None
    assert len(row["raw_text"]) <= 1200
    with sqlite3.connect(tmp_path / "state.db") as conn:
        stored_meta = conn.execute(
            "SELECT metadata_json FROM signal_candidates WHERE id = ?",
            ("sig-big",),
        ).fetchone()[0]
    assert len(stored_meta.encode("utf-8")) <= 16384
    assert "raw_event" not in stored_meta


def test_cleanup_old_records_removes_stale_data_safely(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    old = "2026-03-01T00:00:00Z"
    fresh = "2026-04-06T10:00:00Z"

    db.save_candidate("old-free", "x", "tweet-1", "fp-1", "old free", observed_at=old, metadata={})
    db.save_candidate("old-linked", "x", "tweet-2", "fp-2", "old linked", observed_at=old, metadata={})
    db.save_candidate("old-approved", "x", "tweet-4", "fp-4", "old approved", observed_at=old, metadata={})
    db.save_candidate("fresh-free", "x", "tweet-3", "fp-3", "fresh free", observed_at=fresh, metadata={})
    db.save_decision("old-free", 10, "skip", ["x"], "clanker")
    db.save_decision("old-linked", 10, "skip", ["x"], "clanker")
    db.create_review_item("review-old", "old-linked", expires_at=old)
    db.reject_review_item("review-old", "tester")
    db.create_review_item("review-old-approved", "old-approved", expires_at=old)
    db.lock_review_item("review-old-approved", "tester")
    db.complete_review_item("review-old-approved", success=True, locked_by="tester")
    db.save_deployment_result(
        result_id="dep-old",
        candidate_id="old-linked",
        status="deploy_success",
        deployed_at=old,
        tx_hash="0x" + "a" * 64,
        contract_address="0x" + "b" * 40,
    )
    db.save_reward_claim_result(
        result_id="claim-old",
        token_address="0x" + "c" * 40,
        status="claim_success",
        claimed_at=old,
        tx_hash="0x" + "d" * 64,
    )

    summary = db.cleanup_old_records(
        retention_candidates_days=7,
        retention_reviews_days=7,
        retention_deployments_days=7,
        retention_rewards_days=7,
    )
    assert summary["signal_candidates"] >= 1
    assert db.get_candidate("old-free") is None
    assert db.get_candidate("old-approved") is not None
    assert db.get_candidate("fresh-free") is not None
    assert db.get_candidate("old-linked") is not None


def test_with_retry_uses_exponential_backoff(tmp_path):
    """_with_retry must sleep exponentially on locked-db errors."""
    from unittest.mock import patch
    import sqlite3
    from clankandclaw.database.manager import DatabaseManager

    db = DatabaseManager(tmp_path / "test.db")
    db.initialize()

    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    sleep_calls: list[float] = []
    with patch("clankandclaw.database.manager.sleep", side_effect=lambda t: sleep_calls.append(t)):
        result = db._with_retry(flaky)

    assert result == "ok"
    assert sleep_calls == [0.1, 0.2, 0.4]
