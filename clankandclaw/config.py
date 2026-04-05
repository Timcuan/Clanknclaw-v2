from pathlib import Path
import os
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, Field


class AppSection(BaseModel):
    log_level: str = "INFO"
    review_expiry_seconds: int = 900


class XDetectorSection(BaseModel):
    enabled: bool = True
    poll_interval: float = 30.0
    keywords: list[str] = Field(default_factory=lambda: ["deploy", "launch"])
    max_results: int = 20


class GMGNDetectorSection(BaseModel):
    enabled: bool = True
    poll_interval: float = 60.0
    api_url: str = "https://gmgn.ai/defi/quotation/v1/tokens/base/new"
    max_results: int = 20


class DeploymentSection(BaseModel):
    platform: str = "clanker"
    tax_bps: int = 1000
    base_rpc_url: str = "https://mainnet.base.org"
    executor_path: str = ""  # Path to Clank n Claw - Executor directory
    node_script_path: str = ""  # Override path to clanker_deploy.mjs


class TelegramSection(BaseModel):
    bot_token: str = ""
    chat_id: str = ""


class WalletSection(BaseModel):
    deployer_signer_private_key: str
    token_admin: str
    fee_recipient: str


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    x_detector: XDetectorSection = Field(default_factory=XDetectorSection)
    gmgn_detector: GMGNDetectorSection = Field(default_factory=GMGNDetectorSection)
    deployment: DeploymentSection = Field(default_factory=DeploymentSection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)
    wallets: WalletSection


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text())
    if raw is None:
        raw = {}
    elif not isinstance(raw, Mapping):
        raise ValueError("YAML root must be a mapping")
    else:
        raw = dict(raw)
    # Inject env-var overrides into deployment section
    if "deployment" not in raw:
        raw["deployment"] = {}
    # Env vars take precedence over YAML for deployment settings
    if os.getenv("BASE_RPC_URL"):
        raw["deployment"]["base_rpc_url"] = os.getenv("BASE_RPC_URL")
    if os.getenv("EXECUTOR_PATH"):
        raw["deployment"]["executor_path"] = os.getenv("EXECUTOR_PATH")
    if os.getenv("NODE_SCRIPT_PATH"):
        raw["deployment"]["node_script_path"] = os.getenv("NODE_SCRIPT_PATH")

    # Inject Telegram env vars (env vars take precedence)
    if "telegram" not in raw:
        raw["telegram"] = {}
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        raw["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        raw["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")

    wallets = {
        "deployer_signer_private_key": os.getenv("DEPLOYER_SIGNER_PRIVATE_KEY"),
        "token_admin": os.getenv("TOKEN_ADMIN_ADDRESS"),
        "fee_recipient": os.getenv("FEE_RECIPIENT_ADDRESS"),
    }
    if not wallets["deployer_signer_private_key"]:
        raise ValueError("DEPLOYER_SIGNER_PRIVATE_KEY is required")
    if not wallets["token_admin"]:
        raise ValueError("TOKEN_ADMIN_ADDRESS is required")
    if not wallets["fee_recipient"]:
        raise ValueError("FEE_RECIPIENT_ADDRESS is required")
    raw["wallets"] = wallets
    return AppConfig.model_validate(raw)
