from pathlib import Path

import pytest

from clankandclaw.config import AppConfig, load_config


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


def test_load_config_requires_signer_key(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("app:\n  log_level: INFO\n  review_expiry_seconds: 60\n")
    with pytest.raises(ValueError, match="DEPLOYER_SIGNER_PRIVATE_KEY"):
        load_config(config_file)
