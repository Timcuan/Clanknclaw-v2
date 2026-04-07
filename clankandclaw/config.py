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
    cleanup_enabled: bool = True
    cleanup_interval_seconds: float = 900.0
    retention_candidates_days: int = 3
    retention_reviews_days: int = 7
    retention_deployments_days: int = 14
    retention_rewards_days: int = 30


class StealthConfig(BaseModel):
    enabled: bool = True
    rotate_every: int = 50
    jitter_sigma_pct: float = 0.15
    jitter_min_ms: int = 200
    jitter_max_ms: int = 3000


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
    min_volume_m5_usd: float = 6000.0  # Increased for noise filtering
    min_volume_m15_usd: float = 18000.0
    min_tx_count_m5: int = 15
    min_liquidity_usd: float = 25000.0  # Safe starting point across chains
    max_requests_per_minute: int = 40
    request_timeout_seconds: float = 20.0
    base_target_sources: list[str] = Field(default_factory=lambda: ["bankr", "doppler", "zora", "virtual", "uniswapv4", "clanker", "raydium", "meteora", "orca", "pancakeswap", "aerodrome", "camelot", "v3", "uniswapv3"])
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
    message_thread_id: int | None = None
    thread_review_id: int | None = None
    thread_deploy_id: int | None = None
    thread_claim_id: int | None = None
    thread_ops_id: int | None = None
    thread_alert_id: int | None = None


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
    stealth: StealthConfig = Field(default_factory=StealthConfig)
    wallets: WalletSection


def _parse_positive_int_env(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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
    if os.getenv("APP_CLEANUP_ENABLED"):
        raw["app"]["cleanup_enabled"] = os.getenv("APP_CLEANUP_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    if os.getenv("APP_CLEANUP_INTERVAL_SECONDS"):
        raw["app"]["cleanup_interval_seconds"] = float(os.getenv("APP_CLEANUP_INTERVAL_SECONDS", "900"))
    if os.getenv("APP_RETENTION_CANDIDATES_DAYS"):
        raw["app"]["retention_candidates_days"] = int(os.getenv("APP_RETENTION_CANDIDATES_DAYS", "3"))
    if os.getenv("APP_RETENTION_REVIEWS_DAYS"):
        raw["app"]["retention_reviews_days"] = int(os.getenv("APP_RETENTION_REVIEWS_DAYS", "7"))
    if os.getenv("APP_RETENTION_DEPLOYMENTS_DAYS"):
        raw["app"]["retention_deployments_days"] = int(os.getenv("APP_RETENTION_DEPLOYMENTS_DAYS", "14"))
    if os.getenv("APP_RETENTION_REWARDS_DAYS"):
        raw["app"]["retention_rewards_days"] = int(os.getenv("APP_RETENTION_REWARDS_DAYS", "30"))
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
    message_thread_id = _parse_positive_int_env("TELEGRAM_MESSAGE_THREAD_ID")
    thread_review_id = _parse_positive_int_env("TELEGRAM_THREAD_REVIEW_ID")
    thread_deploy_id = _parse_positive_int_env("TELEGRAM_THREAD_DEPLOY_ID")
    thread_claim_id = _parse_positive_int_env("TELEGRAM_THREAD_CLAIM_ID")
    thread_ops_id = _parse_positive_int_env("TELEGRAM_THREAD_OPS_ID")
    thread_alert_id = _parse_positive_int_env("TELEGRAM_THREAD_ALERT_ID")
    if message_thread_id is not None:
        raw["telegram"]["message_thread_id"] = message_thread_id
    if thread_review_id is not None:
        raw["telegram"]["thread_review_id"] = thread_review_id
    if thread_deploy_id is not None:
        raw["telegram"]["thread_deploy_id"] = thread_deploy_id
    if thread_claim_id is not None:
        raw["telegram"]["thread_claim_id"] = thread_claim_id
    if thread_ops_id is not None:
        raw["telegram"]["thread_ops_id"] = thread_ops_id
    if thread_alert_id is not None:
        raw["telegram"]["thread_alert_id"] = thread_alert_id

    if "farcaster_detector" not in raw:
        raw["farcaster_detector"] = {}
    if os.getenv("NEYNAR_API_KEY"):
        raw["farcaster_detector"]["api_key"] = os.getenv("NEYNAR_API_KEY")

    if "stealth" not in raw:
        raw["stealth"] = {}
    if os.getenv("STEALTH_ENABLED"):
        raw["stealth"]["enabled"] = os.getenv("STEALTH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    if os.getenv("STEALTH_ROTATE_EVERY"):
        raw["stealth"]["rotate_every"] = int(os.getenv("STEALTH_ROTATE_EVERY", "50"))
    if os.getenv("STEALTH_JITTER_SIGMA_PCT"):
        raw["stealth"]["jitter_sigma_pct"] = float(os.getenv("STEALTH_JITTER_SIGMA_PCT", "0.15"))
    if os.getenv("STEALTH_JITTER_MIN_MS"):
        raw["stealth"]["jitter_min_ms"] = int(os.getenv("STEALTH_JITTER_MIN_MS", "200"))
    if os.getenv("STEALTH_JITTER_MAX_MS"):
        raw["stealth"]["jitter_max_ms"] = int(os.getenv("STEALTH_JITTER_MAX_MS", "3000"))

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
