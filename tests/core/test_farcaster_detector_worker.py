import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clankandclaw.core.workers.farcaster_detector_worker import FarcasterDetectorWorker
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


def make_worker(db: DatabaseManager) -> FarcasterDetectorWorker:
    return FarcasterDetectorWorker(
        db=db,
        poll_interval=35.0,
        api_url="https://api.neynar.com/v2/farcaster/cast/search/",
        api_key="test",
        max_results=5,
        target_handles=["bankr", "clanker"],
    )


@pytest.mark.asyncio
async def test_farcaster_worker_starts_and_stops(db):
    worker = make_worker(db)
    await worker.start()
    assert worker._running is True
    await worker.stop()
    assert worker._running is False


@pytest.mark.asyncio
async def test_farcaster_worker_process_event_sends_notification(db):
    worker = make_worker(db)
    telegram = MagicMock()
    telegram.send_review_notification = AsyncMock()
    worker.set_telegram_worker(telegram)
    with patch("clankandclaw.core.workers.farcaster_detector_worker.process_candidate") as mock_process:
        scored = MagicMock()
        scored.decision = "review"
        scored.review_priority = "review"
        scored.score = 77
        scored.reason_codes = ["farcaster_target_intent"]
        mock_process.return_value = scored
        await worker.process_event(
            {
                "id": "fc1",
                "text": "@bankr deploy $MOON",
                "author": {"username": "alice"},
                "created_at": "2026-04-05T12:00:00Z",
                "mentioned_handles": ["bankr"],
            },
            "https://warpcast.com/~/conversations/fc1",
        )
    if worker._notification_tasks:
        await asyncio.gather(*list(worker._notification_tasks))
    telegram.send_review_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_farcaster_worker_sets_billing_blocked_on_402(db):
    worker = make_worker(db)
    mock_response = MagicMock()
    mock_response.status_code = 402
    with patch.object(worker, "_request_with_retry", AsyncMock(return_value=mock_response)):
        processed = await worker._run_feed(MagicMock(), {})

    assert processed == 0
    assert worker._billing_blocked is True
    raw = worker.db.get_runtime_setting("health.farcaster_detector")
    payload = json.loads(raw or "{}")
    assert payload.get("status") == "degraded"
    assert payload.get("reason") == "billing_402"


def test_farcaster_seen_cache_deduplicates_in_o1_pattern(db):
    worker = make_worker(db)
    assert worker._mark_cast_seen("c1") is True
    assert worker._mark_cast_seen("c1") is False
    for i in range(worker._max_seen_cast_ids + 5):
        worker._mark_cast_seen(f"cast-{i}")
    assert len(worker._seen_cast_ids) <= worker._max_seen_cast_ids
    assert len(worker._seen_cast_id_set) <= worker._max_seen_cast_ids


def _make_worker_with_channels(channels=None) -> FarcasterDetectorWorker:
    db = MagicMock()
    db.get_runtime_setting.return_value = None
    return FarcasterDetectorWorker(
        db=db,
        channel_ids=channels or ["clanker", "bankr"],
    )


def test_farcaster_worker_has_channel_ids():
    w = _make_worker_with_channels(channels=["clanker", "bankr"])
    assert "clanker" in w.channel_ids
    assert "bankr" in w.channel_ids


def test_farcaster_worker_channel_ids_default_empty_when_not_set():
    db = MagicMock()
    db.get_runtime_setting.return_value = None
    w = FarcasterDetectorWorker(db=db)
    assert w.channel_ids == []
