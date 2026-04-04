from pathlib import Path
import os

import yaml
from pydantic import BaseModel, Field


class AppSection(BaseModel):
    log_level: str = "INFO"
    review_expiry_seconds: int = 900


class DeploymentSection(BaseModel):
    platform: str = "clanker"
    tax_bps: int = 1000


class WalletSection(BaseModel):
    deployer_signer_private_key: str
    token_admin: str
    fee_recipient: str


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    deployment: DeploymentSection = Field(default_factory=DeploymentSection)
    wallets: WalletSection


def load_config(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text()) or {}
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
