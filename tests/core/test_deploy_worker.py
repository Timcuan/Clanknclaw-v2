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
    worker.preparation.deployer.preflight = AsyncMock(return_value=None)
    worker.preparation.deployer.deploy = AsyncMock(
        return_value=make_deploy_result(status="deploy_failed")
    )

    await worker.prepare_and_deploy("x-2")

    telegram.send_deploy_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_and_deploy_logs_hot_path_step_timings(db, monkeypatch, caplog):
    db.save_candidate(
        "x-3", "x", "tweet-3", "fp-3",
        "deploy token Nova symbol NOVA",
        observed_at="2026-04-05T10:00:00Z",
        metadata={"image_url": "https://example.com/img.png"},
    )
    worker, _, _ = make_worker(db)
    await worker.start()

    async def fake_fetch(url):
        return b"bytes"

    monkeypatch.setattr(
        "clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch
    )
    worker.preparation.pinata.upload_file_bytes = AsyncMock(return_value="QmImg")
    worker.preparation.deployer.preflight = AsyncMock(return_value=None)

    with caplog.at_level("INFO"):
        await worker.prepare_and_deploy("x-3")

    assert any("deploy_worker.lookup_ms=" in r.message for r in caplog.records)
    assert any("deploy_worker.prepare_ms=" in r.message for r in caplog.records)
    assert any("deploy_worker.deploy_ms=" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_prepare_and_deploy_uses_single_candidate_fetch(db):
    worker, _, _ = make_worker(db)
    await worker.start()
    worker.preparation.get_candidate_by_id = AsyncMock(return_value=MagicMock())
    worker.preparation.prepare_deploy_request = AsyncMock(return_value=MagicMock())
    worker.deployer.deploy = AsyncMock(return_value=make_deploy_result())

    await worker.prepare_and_deploy("x-4")

    worker.preparation.get_candidate_by_id.assert_awaited_once_with("x-4")


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


@pytest.mark.asyncio
async def test_prepare_and_deploy_idempotent_skips_already_deployed(db, monkeypatch):
    """Second call for same candidate_id is a no-op if already successfully deployed."""
    from datetime import datetime, timezone

    db.save_candidate("x-5", "x", "tw-5", "fp-5", "deploy token X symbol X", observed_at="2026-04-05T10:00:00Z")
    db.save_deployment_result(
        result_id="dr-1",
        candidate_id="x-5",
        status="deploy_success",
        deployed_at=datetime.now(timezone.utc).isoformat(),
        tx_hash="0x" + "f" * 64,
        contract_address="0x" + "d" * 40,
    )

    worker, _, deployer = make_worker(db)
    await worker.start()

    result = await worker.prepare_and_deploy("x-5")

    assert result is True
    deployer.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_deploys_same_candidate_only_deploy_once(tmp_path):
    """Two concurrent calls for the same candidate_id must result in exactly one deploy."""
    import asyncio
    from unittest.mock import patch

    db = DatabaseManager(tmp_path / "concurrent.db")
    db.initialize()
    db.save_candidate(
        "x-concurrent", "x", "tw-concurrent", "fp-concurrent",
        "deploy token RACE symbol RACE",
        observed_at=_now(),
        metadata={"image_url": "https://example.com/img.png"},
    )

    deploy_call_count = 0
    peak_concurrent = 0
    active = 0

    async def slow_deploy(req):
        nonlocal deploy_call_count, active, peak_concurrent
        active += 1
        peak_concurrent = max(peak_concurrent, active)
        deploy_call_count += 1
        await asyncio.sleep(0.05)
        active -= 1
        return DeployResult(
            deploy_request_id="x-concurrent",
            status="deploy_success",
            tx_hash="0x" + "a" * 64,
            contract_address="0x" + "b" * 40,
            latency_ms=50,
            completed_at=_now(),
        )

    deployer = MagicMock()
    deployer.deploy = slow_deploy
    deployer.preflight = AsyncMock(return_value=None)

    pinata = MagicMock()
    worker = DeployWorker(
        db=db,
        pinata_client=pinata,
        deployer=deployer,
        signer_wallet="0x" + "a" * 40,
        token_admin="0x" + "b" * 40,
        fee_recipient="0x" + "c" * 40,
    )
    await worker.start()

    async def fake_fetch(url):
        return b"bytes"

    with patch("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch):
        worker.preparation.pinata.upload_file_bytes = AsyncMock(return_value="QmImg")
        worker.preparation.deployer.preflight = AsyncMock(return_value=None)
        worker.preparation.deployer.deploy = slow_deploy

        results = await asyncio.gather(
            worker.prepare_and_deploy("x-concurrent"),
            worker.prepare_and_deploy("x-concurrent"),
        )

    assert all(r is True for r in results)
    assert deploy_call_count == 1
    assert peak_concurrent == 1  # lock prevented concurrent entry
