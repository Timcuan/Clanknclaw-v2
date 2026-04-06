from pathlib import Path

import pytest

from clankandclaw.config import AppConfig, TelegramSection, load_config


def test_load_config_reads_yaml_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "app:\n  log_level: DEBUG\n  review_expiry_seconds: 60\n"
        "deployment:\n  platform: clanker\n  tax_bps: 1000\n"
    )
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    cfg = load_config(config_file)
    assert isinstance(cfg, AppConfig)
    assert cfg.app.log_level == "DEBUG"
    assert cfg.wallets.token_admin == "0x0000000000000000000000000000000000000001"


def test_load_config_requires_signer_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.delenv("DEPLOYER_SIGNER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("TOKEN_ADMIN_ADDRESS", raising=False)
    monkeypatch.delenv("FEE_RECIPIENT_ADDRESS", raising=False)
    with pytest.raises(ValueError, match="DEPLOYER_SIGNER_PRIVATE_KEY"):
        load_config(config_file)


@pytest.mark.parametrize(
    ("missing_env", "expected_message"),
    [
        ("TOKEN_ADMIN_ADDRESS", "TOKEN_ADMIN_ADDRESS"),
        ("FEE_RECIPIENT_ADDRESS", "FEE_RECIPIENT_ADDRESS"),
    ],
)
def test_load_config_requires_wallet_addresses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_env: str,
    expected_message: str,
):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.delenv(missing_env, raising=False)
    if missing_env == "TOKEN_ADMIN_ADDRESS":
        monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    else:
        monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    with pytest.raises(ValueError, match=expected_message):
        load_config(config_file)


def test_load_config_reads_telegram_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:AABBCCaabbcc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456")
    monkeypatch.setenv("TELEGRAM_MESSAGE_THREAD_ID", "777")
    cfg = load_config(config_file)
    assert cfg.telegram.bot_token == "1234567890:AABBCCaabbcc"
    assert cfg.telegram.chat_id == "-100123456"
    assert cfg.telegram.message_thread_id == 777


def test_load_config_reads_deployment_overrides_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    monkeypatch.setenv("CLANKER_FEE_BPS", "900")
    monkeypatch.setenv("PAIRED_FEE_BPS", "1100")
    monkeypatch.setenv("TOKEN_ADMIN_ENABLED", "false")
    monkeypatch.setenv("TOKEN_REWARD_ENABLED", "false")
    monkeypatch.setenv("ALCHEMY_BASE_RPC_URL", "https://base-mainnet.g.alchemy.com/v2/testkey")

    cfg = load_config(config_file)
    assert cfg.deployment.clanker_fee_bps == 900
    assert cfg.deployment.paired_fee_bps == 1100
    assert cfg.deployment.token_admin_enabled is False
    assert cfg.deployment.token_reward_enabled is False
    assert cfg.deployment.base_rpc_url == "https://base-mainnet.g.alchemy.com/v2/testkey"


def test_load_config_rejects_non_mapping_yaml_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- not-a-mapping\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    with pytest.raises(ValueError, match="YAML root must be a mapping"):
        load_config(config_file)
