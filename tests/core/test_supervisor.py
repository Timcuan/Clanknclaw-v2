from pathlib import Path

import pytest

from clankandclaw.config import (
    AppConfig, AppSection, DeploymentSection, GMGNDetectorSection,
    TelegramSection, WalletSection, XDetectorSection,
)
from clankandclaw.core.supervisor import Supervisor
from clankandclaw.database.manager import DatabaseManager


def make_config(*, x_enabled: bool = True, gmgn_enabled: bool = True) -> AppConfig:
    return AppConfig(
        app=AppSection(log_level="INFO", review_expiry_seconds=900),
        deployment=DeploymentSection(platform="clanker", tax_bps=1000),
        telegram=TelegramSection(bot_token="", chat_id=""),
        x_detector=XDetectorSection(enabled=x_enabled),
        gmgn_detector=GMGNDetectorSection(enabled=gmgn_enabled),
        wallets=WalletSection(
            deployer_signer_private_key="0xtest",
            token_admin="0x0000000000000000000000000000000000000001",
            fee_recipient="0x0000000000000000000000000000000000000002",
        ),
    )


@pytest.fixture
def test_db(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize()
    return db


def test_supervisor_exposes_worker_names(test_db):
    supervisor = Supervisor(make_config(), test_db)
    assert supervisor.worker_names() == []


@pytest.mark.asyncio
async def test_supervisor_starts_all_workers_when_enabled(test_db):
    supervisor = Supervisor(make_config(x_enabled=True, gmgn_enabled=True), test_db)
    await supervisor.start()
    names = supervisor.worker_names()
    assert "telegram" in names
    assert "x_detector" in names
    assert "gmgn_detector" in names
    await supervisor.stop()
    assert supervisor.worker_names() == []


@pytest.mark.asyncio
async def test_supervisor_respects_x_detector_disabled(test_db):
    supervisor = Supervisor(make_config(x_enabled=False, gmgn_enabled=True), test_db)
    await supervisor.start()
    names = supervisor.worker_names()
    assert "x_detector" not in names
    assert "gmgn_detector" in names
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_respects_gmgn_detector_disabled(test_db):
    supervisor = Supervisor(make_config(x_enabled=True, gmgn_enabled=False), test_db)
    await supervisor.start()
    names = supervisor.worker_names()
    assert "gmgn_detector" not in names
    assert "x_detector" in names
    await supervisor.stop()
