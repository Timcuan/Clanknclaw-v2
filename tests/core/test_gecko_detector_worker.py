"""Tests for GeckoDetectorWorker."""

import asyncio
from datetime import datetime, timedelta, timezone

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clankandclaw.core.workers.gecko_detector_worker import GeckoDetectorWorker
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


def make_worker(db: DatabaseManager) -> GeckoDetectorWorker:
    return GeckoDetectorWorker(
        db=db,
        poll_interval=25.0,
        api_base_url="https://api.geckoterminal.com/api/v2",
        networks=["base"],
        max_results=5,
        min_volume_m5_usd=1000,
        min_volume_m15_usd=2000,
        min_tx_count_m5=4,
        min_liquidity_usd=5000,
        max_requests_per_minute=120,
    )


def make_worker_with_networks(db: DatabaseManager, networks: list[str]) -> GeckoDetectorWorker:
    return GeckoDetectorWorker(
        db=db,
        poll_interval=25.0,
        api_base_url="https://api.geckoterminal.com/api/v2",
        networks=networks,
        max_results=5,
        min_volume_m5_usd=1000,
        min_volume_m15_usd=2000,
        min_tx_count_m5=4,
        min_liquidity_usd=5000,
        max_requests_per_minute=120,
    )


def make_pool(address: str = "0xdeadbeef") -> dict:
    created_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    return {
        "id": f"base_{address}",
        "type": "pool",
        "attributes": {
            "address": address,
            "name": "Moon / WETH",
            "dex_id": "clanker",
            "pool_created_at": created_at,
            "reserve_in_usd": "15000",
            "volume_usd": {"m5": "5000", "m15": "9000"},
            "transactions": {"m5": {"buys": 5, "sells": 3}},
        },
        "relationships": {
            "base_token": {"data": {"id": "base_0xmoon", "type": "token"}},
        },
    }


def make_included_token() -> list[dict]:
    return [
        {
            "id": "base_0xmoon",
            "type": "token",
            "attributes": {
                "name": "Moon",
                "symbol": "MOON",
                "image_url": "https://example.com/moon.png",
            },
        }
    ]


@pytest.mark.asyncio
async def test_gecko_worker_starts_and_stops(db):
    worker = make_worker(db)
    await worker.start()
    assert worker._running is True
    await worker.stop()
    assert worker._running is False


def test_network_priority_orders_base_first(db):
    worker = make_worker_with_networks(db, ["eth", "solana", "base", "bsc"])
    assert worker.networks == ["base", "solana", "bsc", "eth"]


def test_evaluate_pool_stage1_fallback_without_m1_data(db):
    worker = make_worker(db)
    attrs = make_pool("0xstage1")["attributes"]
    is_hot, stats, reason = worker._evaluate_pool("base", attrs)
    assert is_hot is True
    assert reason == "pass"
    assert stats["volume"]["m1"] == 0.0


@pytest.mark.asyncio
async def test_process_payload_sends_review_notification_for_matching_candidate(db):
    worker = make_worker(db)
    telegram = MagicMock()
    telegram.send_review_notification = AsyncMock()
    worker.set_telegram_worker(telegram)

    payload = {
        "id": "base:0xdeadbeef",
        "text": "New launch pool detected on BASE: Moon (MOON)",
        "author": "geckoterminal",
        "token_data": {"image_url": "https://example.com/moon.png"},
        "network": "base",
        "volume": {"m5": 5000.0, "m15": 9000.0},
        "transactions": {"m5": 8},
        "liquidity_usd": 15000.0,
        "hot_score": 5,
    }

    with patch("clankandclaw.core.workers.gecko_detector_worker.process_candidate") as mock_process:
        scored = MagicMock()
        scored.decision = "priority_review"
        scored.review_priority = "priority_review"
        scored.score = 85
        scored.reason_codes = ["gecko_hot_gate"]
        mock_process.return_value = scored

        await worker.process_payload(payload, "https://www.geckoterminal.com/base/pools/0xdeadbeef")

    if worker._notification_tasks:
        await asyncio.gather(*list(worker._notification_tasks))
    telegram.send_review_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_payload_skips_notification_for_low_score(db):
    worker = make_worker(db)
    telegram = MagicMock()
    telegram.send_review_notification = AsyncMock()
    worker.set_telegram_worker(telegram)

    payload = {"id": "base:0xabc", "text": "something", "author": "geckoterminal"}

    with patch("clankandclaw.core.workers.gecko_detector_worker.process_candidate") as mock_process:
        scored = MagicMock()
        scored.decision = "skip"
        scored.reason_codes = ["gecko_not_hot"]
        mock_process.return_value = scored

        await worker.process_payload(payload, "https://www.geckoterminal.com/base/pools/0xabc")

    telegram.send_review_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_seen_pool_ids_deduplication(db):
    worker = make_worker(db)
    worker.set_telegram_worker(MagicMock())

    processed = []

    async def fake_process(payload, context_url):
        processed.append(payload["id"])

    worker.process_payload = fake_process  # type: ignore

    pool = make_pool("0xaaaa")
    response_data = {"data": [pool, pool], "included": make_included_token()}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await worker._poll_and_process()

    assert processed.count("base:0xaaaa") == 1


@pytest.mark.asyncio
async def test_poll_uses_gecko_context_url(db):
    worker = make_worker(db)
    captured_urls = []

    async def fake_process(payload, context_url):
        captured_urls.append(context_url)

    worker.process_payload = fake_process  # type: ignore

    pool = make_pool("0xbbbb")
    response_data = {"data": [pool], "included": make_included_token()}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_response = MagicMock()
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        await worker._poll_and_process()

    assert captured_urls == ["https://www.geckoterminal.com/base/pools/0xbbbb"]
