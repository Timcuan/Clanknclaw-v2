"""Tests for TelegramWorker."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clankandclaw.core.workers.telegram_worker import TelegramWorker
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.rewards.claimer import ClaimFeesResult


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    manager.save_candidate(
        "x-1", "x", "tweet-1", "fp-1", "deploy token Moon symbol MOON",
        observed_at="2026-04-05T10:00:00Z",
    )
    return manager


def make_worker(db: DatabaseManager) -> TelegramWorker:
    return TelegramWorker(db=db, review_expiry_seconds=900)


def make_mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_review_notification = AsyncMock(return_value=42)
    bot.send_deploy_preparing = AsyncMock()
    bot.send_deploy_success = AsyncMock()
    bot.send_deploy_failure = AsyncMock()
    bot.start_polling = AsyncMock()
    bot.stop = AsyncMock()
    return bot


# ── start / stop ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_worker_starts_without_bot_token(db):
    """Worker disables gracefully when bot token is absent (no env var)."""
    worker = make_worker(db)
    # No TELEGRAM_BOT_TOKEN set — TelegramBot.__init__ should raise ValueError
    # Worker catches it and stays not running
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", side_effect=ValueError("missing token")):
        await worker.start()
    assert worker._running is False
    assert worker._bot is None


@pytest.mark.asyncio
async def test_worker_starts_and_stops_with_mock_bot(db):
    bot = make_mock_bot()
    bot.start_polling = AsyncMock()

    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    assert worker._running is True
    assert worker._bot is bot

    await worker.stop()
    assert worker._running is False
    bot.stop.assert_awaited_once()


# ── send_review_notification ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_review_notification_creates_review_item(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    review_id = await worker.send_review_notification("x-1", "priority_review", 85, ["kw"])

    assert review_id == "review-x-1"
    row = db.get_review_item("review-x-1")
    assert row is not None
    assert row["candidate_id"] == "x-1"
    assert row["status"] == "pending"
    bot.send_review_notification.assert_awaited_once_with(
        "x-1", "priority_review", 85, ["kw"],
        raw_text="deploy token Moon symbol MOON",
        source="x",
        context_url=None,
        author_handle=None,
        metadata={},
    )


@pytest.mark.asyncio
async def test_send_review_notification_uses_candidate_row_without_extra_defaults(db, caplog):
    db.save_candidate(
        "x-2", "x", "tweet-2", "fp-2", "deploy token Star symbol STAR",
        observed_at="2026-04-05T10:00:00Z",
        metadata={
            "context_url": "https://x.com/bob/status/2",
            "author_handle": "bob",
            "image_url": "https://example.com/img.png",
        },
    )

    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    with caplog.at_level("INFO"):
        await worker.send_review_notification("x-2", "priority_review", 88, ["kw"])

    kwargs = bot.send_review_notification.await_args.kwargs
    assert kwargs["raw_text"] == "deploy token Star symbol STAR"
    assert kwargs["source"] == "x"
    assert kwargs["context_url"] == "https://x.com/bob/status/2"
    assert kwargs["author_handle"] == "bob"
    assert kwargs["metadata"]["context_url"] == "https://x.com/bob/status/2"
    assert any("telegram.review_notify_ms=" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_send_review_notification_returns_none_when_not_running(db):
    worker = make_worker(db)
    # Don't start — bot is None
    result = await worker.send_review_notification("x-1", "review", 50, [])
    assert result is None


@pytest.mark.asyncio
async def test_send_review_notification_returns_none_when_bot_send_fails(db):
    bot = make_mock_bot()
    bot.send_review_notification = AsyncMock(return_value=None)  # send fails
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    result = await worker.send_review_notification("x-1", "review", 50, [])
    assert result is None


# ── _handle_approve ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_approve_locks_review_and_triggers_deploy(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    # Create a pending review item
    await worker.send_review_notification("x-1", "review", 70, [])

    deploy_prep = MagicMock()
    deploy_prep.prepare_and_deploy = AsyncMock()
    worker.set_deploy_preparation(deploy_prep)

    await worker._handle_approve("x-1")

    deploy_prep.prepare_and_deploy.assert_awaited_once_with("x-1")
    row = db.get_review_item("review-x-1")
    assert row["status"] == "deploying"
    assert row["locked_by"] == "telegram"


@pytest.mark.asyncio
async def test_handle_approve_raises_when_review_not_found(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    with pytest.raises(ValueError, match="already processed or expired"):
        await worker._handle_approve("x-1")  # no review item created


@pytest.mark.asyncio
async def test_handle_approve_prevents_double_approval(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    await worker.send_review_notification("x-1", "review", 70, [])

    deploy_prep = MagicMock()
    deploy_prep.prepare_and_deploy = AsyncMock()
    worker.set_deploy_preparation(deploy_prep)

    await worker._handle_approve("x-1")  # first approval — OK

    with pytest.raises(ValueError, match="already processed or expired"):
        await worker._handle_approve("x-1")  # second — must fail

    deploy_prep.prepare_and_deploy.assert_awaited_once()  # only called once


# ── _handle_reject ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_reject_marks_rejected_without_deploy(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    await worker.send_review_notification("x-1", "review", 70, [])

    deploy_prep = MagicMock()
    deploy_prep.prepare_and_deploy = AsyncMock()
    worker.set_deploy_preparation(deploy_prep)

    await worker._handle_reject("x-1")

    deploy_prep.prepare_and_deploy.assert_not_awaited()
    row = db.get_review_item("review-x-1")
    assert row["status"] == "rejected"


# ── deploy result notifications ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_deploy_success_delegates_to_bot(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    await worker.send_deploy_success("x-1", "0x" + "a" * 64, "0x" + "b" * 40)
    bot.send_deploy_success.assert_awaited_once_with("x-1", "0x" + "a" * 64, "0x" + "b" * 40)


@pytest.mark.asyncio
async def test_send_deploy_failure_delegates_to_bot(db):
    bot = make_mock_bot()
    worker = make_worker(db)
    with patch("clankandclaw.core.workers.telegram_worker.TelegramBot", return_value=bot):
        await worker.start()

    await worker.send_deploy_failure("x-1", "sdk_error", "something broke")
    bot.send_deploy_failure.assert_awaited_once_with("x-1", "sdk_error", "something broke")


@pytest.mark.asyncio
async def test_send_notifications_noop_when_bot_absent(db):
    worker = make_worker(db)
    # No bot — should not raise
    await worker.send_deploy_success("x-1", "0x" + "a" * 64, "0x" + "b" * 40)
    await worker.send_deploy_failure("x-1", "err", "msg")


@pytest.mark.asyncio
async def test_handle_claim_fees_persists_result(db):
    worker = make_worker(db)
    claimer = MagicMock()
    claimer.claim = AsyncMock(
        return_value=ClaimFeesResult(
            status="claim_success",
            tx_hash="0x" + "a" * 64,
        )
    )
    worker.set_rewards_claimer(claimer)

    result = await worker._handle_claim_fees("0x" + "b" * 40)
    assert result.status == "claim_success"

    import sqlite3

    with sqlite3.connect(db.path) as conn:
        row = conn.execute(
            "SELECT token_address, status, tx_hash FROM reward_claim_results ORDER BY claimed_at DESC LIMIT 1"
        ).fetchone()
    assert row == ("0x" + "b" * 40, "claim_success", "0x" + "a" * 64)
