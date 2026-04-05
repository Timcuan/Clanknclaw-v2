import sqlite3

import pytest

from clankandclaw.database.manager import DatabaseManager


def test_database_manager_initializes_schema(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    tables = db.list_tables()
    assert "signal_candidates" in tables
    assert "review_items" in tables


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
