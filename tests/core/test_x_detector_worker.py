from pathlib import Path

from clankandclaw.core.workers.x_detector_worker import XDetectorWorker
from clankandclaw.database.manager import DatabaseManager


def _make_worker(tmp_path: Path) -> XDetectorWorker:
    db = DatabaseManager(tmp_path / "x-worker.db")
    db.initialize()
    return XDetectorWorker(db=db)


def test_x_worker_seen_cache_deduplicates(tmp_path: Path):
    worker = _make_worker(tmp_path)
    assert worker._mark_tweet_seen("t1") is True
    assert worker._mark_tweet_seen("t1") is False


def test_x_worker_seen_cache_bounded_size(tmp_path: Path):
    worker = _make_worker(tmp_path)
    for i in range(worker._max_seen_tweet_ids + 7):
        worker._mark_tweet_seen(f"tweet-{i}")
    assert len(worker._seen_tweet_ids) <= worker._max_seen_tweet_ids
    assert len(worker._seen_tweet_id_set) <= worker._max_seen_tweet_ids
