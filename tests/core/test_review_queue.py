from clankandclaw.core.review_queue import ReviewQueue
from clankandclaw.database.manager import DatabaseManager


def test_review_queue_locks_item_once(tmp_path):
    db = DatabaseManager(tmp_path / "state.db")
    db.initialize()
    db.save_candidate("sig-1", "x", "tweet-1", "fp-1", "deploy pepe")
    queue = ReviewQueue(db)
    queue.create("review-1", "sig-1", "2099-01-01T00:00:00Z")
    assert queue.lock("review-1", "telegram") is True
    assert queue.lock("review-1", "telegram") is False
