from pathlib import Path

import pytest

from clankandclaw.config import AppConfig, AppSection, DeploymentSection, TelegramSection, WalletSection
from clankandclaw.core.supervisor import Supervisor
from clankandclaw.database.manager import DatabaseManager


@pytest.fixture
def test_config():
    """Create a test configuration."""
    return AppConfig(
        app=AppSection(log_level="INFO", review_expiry_seconds=900),
        deployment=DeploymentSection(platform="clanker", tax_bps=1000),
        telegram=TelegramSection(bot_token="", chat_id=""),
        wallets=WalletSection(
            deployer_signer_private_key="0xtest",
            token_admin="0x0000000000000000000000000000000000000001",
            fee_recipient="0x0000000000000000000000000000000000000002",
        ),
    )


@pytest.fixture
def test_db(tmp_path: Path):
    """Create a test database."""
    db = DatabaseManager(tmp_path / "test.db")
    db.initialize()
    return db


def test_supervisor_exposes_worker_names(test_config, test_db):
    """Test that supervisor exposes worker names."""
    supervisor = Supervisor(test_config, test_db)
    # Initially no workers until start() is called
    assert supervisor.worker_names() == []


@pytest.mark.asyncio
async def test_supervisor_starts_and_stops_workers(test_config, test_db):
    """Test that supervisor can start and stop workers."""
    supervisor = Supervisor(test_config, test_db)
    
    await supervisor.start()
    worker_names = supervisor.worker_names()
    assert "x_detector" in worker_names
    assert "gmgn_detector" in worker_names
    assert "telegram" in worker_names
    
    await supervisor.stop()
    assert supervisor.worker_names() == []
