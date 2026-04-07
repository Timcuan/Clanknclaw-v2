from pathlib import Path

import pytest

from clankandclaw.config import AppConfig, TelegramSection, load_config, StealthConfig


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
    monkeypatch.setenv("TELEGRAM_THREAD_REVIEW_ID", "1001")
    monkeypatch.setenv("TELEGRAM_THREAD_DEPLOY_ID", "1002")
    monkeypatch.setenv("TELEGRAM_THREAD_CLAIM_ID", "1003")
    monkeypatch.setenv("TELEGRAM_THREAD_OPS_ID", "1004")
    monkeypatch.setenv("TELEGRAM_THREAD_ALERT_ID", "1005")
    cfg = load_config(config_file)
    assert cfg.telegram.bot_token == "1234567890:AABBCCaabbcc"
    assert cfg.telegram.chat_id == "-100123456"
    assert cfg.telegram.message_thread_id == 777
    assert cfg.telegram.thread_review_id == 1001
    assert cfg.telegram.thread_deploy_id == 1002
    assert cfg.telegram.thread_claim_id == 1003
    assert cfg.telegram.thread_ops_id == 1004
    assert cfg.telegram.thread_alert_id == 1005


def test_load_config_ignores_non_positive_telegram_thread_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:AABBCCaabbcc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456")
    monkeypatch.setenv("TELEGRAM_MESSAGE_THREAD_ID", "0")
    monkeypatch.setenv("TELEGRAM_THREAD_REVIEW_ID", "-1")
    monkeypatch.setenv("TELEGRAM_THREAD_DEPLOY_ID", "abc")

    cfg = load_config(config_file)
    assert cfg.telegram.message_thread_id is None
    assert cfg.telegram.thread_review_id is None
    assert cfg.telegram.thread_deploy_id is None


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


def test_load_config_reads_cleanup_overrides_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    monkeypatch.setenv("APP_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("APP_CLEANUP_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("APP_RETENTION_CANDIDATES_DAYS", "2")
    monkeypatch.setenv("APP_RETENTION_REVIEWS_DAYS", "5")
    monkeypatch.setenv("APP_RETENTION_DEPLOYMENTS_DAYS", "10")
    monkeypatch.setenv("APP_RETENTION_REWARDS_DAYS", "20")

    cfg = load_config(config_file)
    assert cfg.app.cleanup_enabled is True
    assert cfg.app.cleanup_interval_seconds == 300
    assert cfg.app.retention_candidates_days == 2
    assert cfg.app.retention_reviews_days == 5
    assert cfg.app.retention_deployments_days == 10
    assert cfg.app.retention_rewards_days == 20


def test_load_config_rejects_non_mapping_yaml_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- not-a-mapping\n")
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0xabc")
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x0000000000000000000000000000000000000002")
    with pytest.raises(ValueError, match="YAML root must be a mapping"):
        load_config(config_file)


def _wallets_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOYER_SIGNER_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setenv("TOKEN_ADMIN_ADDRESS", "0x" + "b" * 40)
    monkeypatch.setenv("FEE_RECIPIENT_ADDRESS", "0x" + "c" * 40)


def test_stealth_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _wallets_env(monkeypatch)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("")
    config = load_config(cfg_path)
    assert config.stealth.enabled is True
    assert config.stealth.rotate_every == 50
    assert config.stealth.jitter_sigma_pct == 0.15
    assert config.stealth.jitter_min_ms == 200
    assert config.stealth.jitter_max_ms == 3000


def test_stealth_config_yaml_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _wallets_env(monkeypatch)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("stealth:\n  enabled: false\n  rotate_every: 10\n")
    config = load_config(cfg_path)
    assert config.stealth.enabled is False
    assert config.stealth.rotate_every == 10


def test_stealth_config_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _wallets_env(monkeypatch)
    monkeypatch.setenv("STEALTH_ENABLED", "false")
    monkeypatch.setenv("STEALTH_ROTATE_EVERY", "25")
    monkeypatch.setenv("STEALTH_JITTER_MIN_MS", "500")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("")
    config = load_config(cfg_path)
    assert config.stealth.enabled is False
    assert config.stealth.rotate_every == 25
    assert config.stealth.jitter_min_ms == 500
