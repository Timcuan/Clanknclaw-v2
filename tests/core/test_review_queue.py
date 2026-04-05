from clankandclaw.core.review_queue import ReviewQueue
from clankandclaw.database.manager import DatabaseManager


def test_review_queue_persists_metadata_and_locks_once(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    queue = ReviewQueue(db)

    timestamps = iter(["2026-04-05T00:00:00Z", "2026-04-05T00:05:00Z", "2026-04-05T00:06:00Z"])
    monkeypatch.setattr(
        "clankandclaw.database.manager._utc_now_iso",
        lambda: next(timestamps),
    )

    queue.create("review-1", "sig-1", "2099-01-01T00:00:00Z")
    created_row = db.get_review_item("review-1")
    assert created_row is not None
    assert created_row["candidate_id"] == "sig-1"
    assert created_row["status"] == "pending"
    assert created_row["created_at"] == "2026-04-05T00:00:00Z"
    assert created_row["locked_by"] is None
    assert created_row["locked_at"] is None

    assert queue.lock("review-1", "telegram") is True
    locked_row = db.get_review_item("review-1")
    assert locked_row is not None
    assert locked_row["status"] == "deploying"
    assert locked_row["locked_by"] == "telegram"
    assert locked_row["locked_at"] == "2026-04-05T00:05:00Z"

    assert queue.lock("review-1", "telegram") is False
    assert db.get_review_item("review-1")["locked_at"] == "2026-04-05T00:05:00Z"


def test_review_queue_lock_rejects_expired_review(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-2", "x", "tweet-2", "fp-2", "deploy test")
    queue = ReviewQueue(db)

    timestamps = iter(["2026-04-05T00:00:00Z", "2026-04-05T02:00:00Z"])
    monkeypatch.setattr(
        "clankandclaw.database.manager._utc_now_iso",
        lambda: next(timestamps),
    )

    # Create review with expiry 1 minute in the future (at creation time)
    queue.create("review-2", "sig-2", "2026-04-05T00:01:00Z")
    # Attempt to lock 2 hours later (past expiry)
    assert queue.lock("review-2", "telegram") is False
    assert db.get_review_item("review-2")["status"] == "pending"

