"""Tests for DeployWorker."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clankandclaw.core.workers.deploy_worker import DeployWorker
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import DeployResult


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_deploy_result(*, status: str = "deploy_success") -> DeployResult:
    if status == "deploy_success":
        return DeployResult(
            deploy_request_id="x-1",
            status="deploy_success",
            tx_hash="0x" + "a" * 64,
            contract_address="0x" + "b" * 40,
            latency_ms=100,
            completed_at=_now(),
        )
    return DeployResult(
        deploy_request_id="x-1",
        status="deploy_failed",
        latency_ms=0,
        error_code="sdk_error",
        error_message="something went wrong",
        completed_at=_now(),
    )


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


def make_worker(db: DatabaseManager) -> tuple[DeployWorker, MagicMock, MagicMock]:
    pinata = MagicMock()
    deployer = MagicMock()
    deployer.deploy = AsyncMock(return_value=make_deploy_result())

    worker = DeployWorker(
        db=db,
        pinata_client=pinata,
        deployer=deployer,
        signer_wallet="0x" + "a" * 40,
        token_admin="0x" + "b" * 40,
        fee_recipient="0x" + "c" * 40,
        tax_bps=1000,
    )
    return worker, pinata, deployer


# ── lifecycle ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deploy_worker_starts_and_stops(db):
    worker, _, _ = make_worker(db)
    await worker.start()
    assert worker._running is True
    await worker.stop()
    assert worker._running is False


# ── prepare_and_deploy ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_and_deploy_notifies_success(db, monkeypatch):
    db.save_candidate(
        "x-1", "x", "tweet-1", "fp-1",
        "deploy token Moon symbol MOON",
        observed_at="2026-04-05T10:00:00Z",
        metadata={"image_url": "https://example.com/img.png"},
    )
    worker, _, deployer = make_worker(db)
    await worker.start()

    telegram = MagicMock()
    telegram.send_deploy_success = AsyncMock()
    telegram.send_deploy_failure = AsyncMock()
    worker.set_telegram_worker(telegram)

    # Patch fetch_image_bytes and pinata so preparation succeeds
    async def fake_fetch(url):
        return b"bytes"

    monkeypatch.setattr(
        "clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch
    )
    worker.preparation.pinata.upload_file_bytes = AsyncMock(return_value="QmImg")
    worker.preparation.pinata.upload_json_metadata = AsyncMock(return_value="QmMeta")
    worker.preparation.deployer.preflight = AsyncMock(return_value=None)

    await worker.prepare_and_deploy("x-1")

    telegram.send_deploy_success.assert_awaited_once()
    call_args = telegram.send_deploy_success.call_args[0]
    assert call_args[0] == "x-1"
    assert call_args[1] == "0x" + "a" * 64


@pytest.mark.asyncio
async def test_prepare_and_deploy_notifies_failure_on_missing_candidate(db):
    worker, _, _ = make_worker(db)
    await worker.start()

    telegram = MagicMock()
    telegram.send_deploy_failure = AsyncMock()
    worker.set_telegram_worker(telegram)

    await worker.prepare_and_deploy("nonexistent")

    telegram.send_deploy_failure.assert_awaited_once()
    args = telegram.send_deploy_failure.call_args[0]
    assert args[0] == "nonexistent"


@pytest.mark.asyncio
async def test_prepare_and_deploy_notifies_failure_on_deploy_failure(db, monkeypatch):
    db.save_candidate(
        "x-2", "x", "tweet-2", "fp-2",
        "deploy token Star symbol STAR",
        observed_at="2026-04-05T10:00:00Z",
        metadata={"image_url": "https://example.com/img.png"},
    )
    worker, _, deployer = make_worker(db)
    deployer.deploy = AsyncMock(return_value=make_deploy_result(status="deploy_failed"))
    await worker.start()

    telegram = MagicMock()
    telegram.send_deploy_success = AsyncMock()
    telegram.send_deploy_failure = AsyncMock()
    worker.set_telegram_worker(telegram)

    async def fake_fetch(url):
        return b"bytes"

    monkeypatch.setattr(
        "clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch
    )
    worker.preparation.pinata.upload_file_bytes = AsyncMock(return_value="QmImg")
    worker.preparation.pinata.upload_json_metadata = AsyncMock(return_value="QmMeta")
    worker.preparation.deployer.preflight = AsyncMock(return_value=None)
    worker.preparation.deployer.deploy = AsyncMock(
        return_value=make_deploy_result(status="deploy_failed")
    )

    await worker.prepare_and_deploy("x-2")

    telegram.send_deploy_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_and_deploy_skips_when_not_running(db):
    worker, _, deployer = make_worker(db)
    # Don't call start() — worker._running is False

    telegram = MagicMock()
    telegram.send_deploy_failure = AsyncMock()
    worker.set_telegram_worker(telegram)

    await worker.prepare_and_deploy("x-1")

    deployer.deploy.assert_not_awaited()
    telegram.send_deploy_failure.assert_not_awaited()
