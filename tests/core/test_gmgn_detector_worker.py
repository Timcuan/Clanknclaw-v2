"""Tests for GMGNDetectorWorker."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clankandclaw.core.workers.gmgn_detector_worker import GMGNDetectorWorker
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


def make_worker(db: DatabaseManager) -> GMGNDetectorWorker:
    return GMGNDetectorWorker(
        db=db,
        poll_interval=60.0,
        api_url="https://gmgn.ai/defi/quotation/v1/tokens/base/new",
        max_results=5,
    )


def make_token(address: str = "0xdeadbeef") -> dict:
    return {
        "address": address,
        "name": "Moon",
        "symbol": "MOON",
        "logo": "https://example.com/moon.png",
    }


# ── lifecycle ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gmgn_worker_starts_and_stops(db):
    worker = make_worker(db)
    await worker.start()
    assert worker._running is True
    await worker.stop()
    assert worker._running is False


# ── process_payload ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_payload_sends_review_notification_for_matching_candidate(db):
    worker = make_worker(db)
    telegram = MagicMock()
    telegram.send_review_notification = AsyncMock()
    worker.set_telegram_worker(telegram)

    payload = {
        "id": "0xdeadbeef",
        "text": "New token launch: Moon (MOON)",
        "author": "gmgn",
        "token_data": {"logo": "https://example.com/moon.png"},
    }

    with patch("clankandclaw.core.workers.gmgn_detector_worker.process_candidate") as mock_process:
        scored = MagicMock()
        scored.decision = "priority_review"
        scored.review_priority = "priority_review"
        scored.score = 80
        scored.reason_codes = ["keyword_match"]
        mock_process.return_value = scored

        await worker.process_payload(payload, "https://gmgn.ai/base/token/0xdeadbeef")

    telegram.send_review_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_payload_skips_notification_for_low_score(db):
    worker = make_worker(db)
    telegram = MagicMock()
    telegram.send_review_notification = AsyncMock()
    worker.set_telegram_worker(telegram)

    payload = {"id": "0xabc", "text": "something", "author": "gmgn"}

    with patch("clankandclaw.core.workers.gmgn_detector_worker.process_candidate") as mock_process:
        scored = MagicMock()
        scored.decision = "skip"
        scored.reason_codes = ["no_signal"]
        mock_process.return_value = scored

        await worker.process_payload(payload, "https://gmgn.ai/base/token/0xabc")

    telegram.send_review_notification.assert_not_awaited()


# ── deduplication ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seen_tokens_deduplication(db):
    worker = make_worker(db)
    worker.set_telegram_worker(MagicMock())

    processed = []

    async def fake_process(payload, context_url):
        processed.append(payload["id"])

    worker.process_payload = fake_process  # type: ignore

    token = make_token("0xaaaa")
    response_data = {"data": {"tokens": [token, token]}}  # same token twice

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

    assert processed.count("0xaaaa") == 1


@pytest.mark.asyncio
async def test_seen_tokens_deque_evicts_oldest(db):
    """deque(maxlen=1000) should evict oldest entries, not arbitrary ones."""
    worker = make_worker(db)
    # Fill up to maxlen
    for i in range(1000):
        worker._seen_tokens.append(f"0x{i:040x}")

    assert len(worker._seen_tokens) == 1000
    # Adding one more should evict the first
    worker._seen_tokens.append("0xnew")
    assert len(worker._seen_tokens) == 1000
    assert "0xnew" in worker._seen_tokens
    assert f"0x{'0':040}" not in worker._seen_tokens  # first one evicted


# ── context URL ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poll_uses_base_context_url(db):
    worker = make_worker(db)
    captured_urls = []

    async def fake_process(payload, context_url):
        captured_urls.append(context_url)

    worker.process_payload = fake_process  # type: ignore

    token = make_token("0xbbbb")
    response_data = {"data": {"tokens": [token]}}

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

    assert captured_urls == ["https://gmgn.ai/base/token/0xbbbb"]


# ── build_token_description ───────────────────────────────────────────────────

def test_build_token_description_includes_name_and_symbol(db):
    worker = make_worker(db)
    desc = worker._build_token_description({"name": "Moon", "symbol": "MOON"})
    assert "Moon" in desc
    assert "MOON" in desc


def test_build_token_description_includes_social_links(db):
    worker = make_worker(db)
    desc = worker._build_token_description({
        "name": "Moon", "symbol": "MOON",
        "twitter": "https://twitter.com/moon",
        "website": "https://moon.xyz",
    })
    assert "twitter.com/moon" in desc
    assert "moon.xyz" in desc
