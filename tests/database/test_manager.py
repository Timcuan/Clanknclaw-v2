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
            CREATE TABLE review_items (
                id TEXT PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )

    db = DatabaseManager(db_path)
    db.initialize()

    columns = {
        row[1]
        for row in sqlite3.connect(db_path).execute("PRAGMA table_info(review_items)").fetchall()
    }
    assert {"created_at", "locked_by", "locked_at"}.issubset(columns)

    monkeypatch.setattr("clankandclaw.database.manager._utc_now_iso", lambda: "2026-04-05T00:00:00Z")
    db.create_review_item("review-1", "sig-1", "2099-01-01T00:00:00Z")

    row = db.get_review_item("review-1")
    assert row is not None
    assert row["candidate_id"] == "sig-1"
    assert row["status"] == "pending"
    assert row["created_at"] == "2026-04-05T00:00:00Z"
    assert row["locked_by"] is None
    assert row["locked_at"] is None
