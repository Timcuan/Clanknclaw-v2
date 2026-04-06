import asyncio
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
    worker.process_event = AsyncMock()  # should not be called

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.status_code = 402
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await worker._poll_and_process()

    assert worker._billing_blocked is True
    worker.process_event.assert_not_awaited()


def test_farcaster_seen_cache_deduplicates_in_o1_pattern(db):
    worker = make_worker(db)
    assert worker._mark_cast_seen("c1") is True
    assert worker._mark_cast_seen("c1") is False
    for i in range(worker._max_seen_cast_ids + 5):
        worker._mark_cast_seen(f"cast-{i}")
    assert len(worker._seen_cast_ids) <= worker._max_seen_cast_ids
    assert len(worker._seen_cast_id_set) <= worker._max_seen_cast_ids
