from pathlib import Path
import os
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, Field


class AppSection(BaseModel):
    log_level: str = "INFO"
    review_expiry_seconds: int = 900
    user_agent: str = "ClankAndClaw/1.0 (+ops)"
    worker_loop_timeout_seconds: float = 90.0
    candidate_process_timeout_seconds: float = 20.0
    max_pending_notifications: int = 500
    deploy_prepare_timeout_seconds: float = 90.0
    deploy_execute_timeout_seconds: float = 180.0


class XDetectorSection(BaseModel):
    enabled: bool = True
    poll_interval: float = 30.0
    keywords: list[str] = Field(default_factory=lambda: ["deploy", "launch"])
    max_results: int = 20
    target_handles: list[str] = Field(default_factory=lambda: ["bankrbot", "clankerdeploy"])
    query_terms: list[str] = Field(default_factory=lambda: ["deploy", "launch", "contract", "ca", "token"])
    max_process_concurrency: int = 8
    max_query_concurrency: int = 3


class FarcasterDetectorSection(BaseModel):
    enabled: bool = True
    poll_interval: float = 35.0
    api_url: str = "https://api.neynar.com/v2/farcaster/cast/search/"
    api_key: str = ""
    max_results: int = 20
    target_handles: list[str] = Field(default_factory=lambda: ["bankr", "clanker"])
    query_terms: list[str] = Field(default_factory=lambda: ["deploy", "launch", "contract", "ca", "token"])
    request_timeout_seconds: float = 20.0
    max_requests_per_minute: int = 45
    max_process_concurrency: int = 8
    max_query_concurrency: int = 2


class GeckoDetectorSection(BaseModel):
    enabled: bool = True
    poll_interval: float = 25.0
    api_base_url: str = "https://api.geckoterminal.com/api/v2"
    networks: list[str] = Field(default_factory=lambda: ["base", "eth", "solana", "bsc"])
    max_results: int = 20
    max_pool_age_minutes: int = 120
    min_volume_m5_usd: float = 3000.0
    min_volume_m15_usd: float = 8000.0
    min_tx_count_m5: int = 12
    min_liquidity_usd: float = 12000.0
    max_requests_per_minute: int = 40
    request_timeout_seconds: float = 20.0
    base_target_sources: list[str] = Field(default_factory=lambda: ["bankr", "doppler", "zora", "virtual", "uniswapv4", "clanker"])
    max_process_concurrency: int = 10


class DeploymentSection(BaseModel):
    platform: str = "clanker"
    tax_bps: int = 1000
    clanker_fee_bps: int | None = None
    paired_fee_bps: int | None = None
    token_admin_enabled: bool = True
    token_reward_enabled: bool = True
    base_rpc_url: str = "https://mainnet.base.org"
    clanker_node_modules_path: str = ""  # Optional override path to node_modules
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
    farcaster_detector: FarcasterDetectorSection = Field(default_factory=FarcasterDetectorSection)
    gecko_detector: GeckoDetectorSection = Field(default_factory=GeckoDetectorSection)
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
    if "app" not in raw:
        raw["app"] = {}
    if os.getenv("APP_USER_AGENT"):
        raw["app"]["user_agent"] = os.getenv("APP_USER_AGENT")
    # Backward compatibility: migrate gmgn_detector block to gecko_detector if needed.
    if "gecko_detector" not in raw and "gmgn_detector" in raw:
        raw["gecko_detector"] = raw["gmgn_detector"]
    # Inject env-var overrides into deployment section
    if "deployment" not in raw:
        raw["deployment"] = {}
    # Env vars take precedence over YAML for deployment settings
    # Prefer dedicated Alchemy endpoint when available
    if os.getenv("ALCHEMY_BASE_RPC_URL"):
        raw["deployment"]["base_rpc_url"] = os.getenv("ALCHEMY_BASE_RPC_URL")
    elif os.getenv("ALCHEMY_RPC"):
        raw["deployment"]["base_rpc_url"] = os.getenv("ALCHEMY_RPC")
    elif os.getenv("BASE_RPC_URL"):
        raw["deployment"]["base_rpc_url"] = os.getenv("BASE_RPC_URL")
    if os.getenv("CLANKER_FEE_BPS"):
        raw["deployment"]["clanker_fee_bps"] = int(os.getenv("CLANKER_FEE_BPS", "0"))
    if os.getenv("PAIRED_FEE_BPS"):
        raw["deployment"]["paired_fee_bps"] = int(os.getenv("PAIRED_FEE_BPS", "0"))
    if os.getenv("TOKEN_ADMIN_ENABLED"):
        raw["deployment"]["token_admin_enabled"] = os.getenv("TOKEN_ADMIN_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    if os.getenv("TOKEN_REWARD_ENABLED"):
        raw["deployment"]["token_reward_enabled"] = os.getenv("TOKEN_REWARD_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    if os.getenv("CLANKER_NODE_MODULES_PATH"):
        raw["deployment"]["clanker_node_modules_path"] = os.getenv("CLANKER_NODE_MODULES_PATH")
    if os.getenv("NODE_SCRIPT_PATH"):
        raw["deployment"]["node_script_path"] = os.getenv("NODE_SCRIPT_PATH")

    # Inject Telegram env vars (env vars take precedence)
    if "telegram" not in raw:
        raw["telegram"] = {}
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        raw["telegram"]["bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
    if os.getenv("TELEGRAM_CHAT_ID"):
        raw["telegram"]["chat_id"] = os.getenv("TELEGRAM_CHAT_ID")

    if "farcaster_detector" not in raw:
        raw["farcaster_detector"] = {}
    if os.getenv("NEYNAR_API_KEY"):
        raw["farcaster_detector"]["api_key"] = os.getenv("NEYNAR_API_KEY")

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
