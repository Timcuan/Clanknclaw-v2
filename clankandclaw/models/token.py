import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator


_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_TX_HASH_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _validate_iso_datetime(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be a valid ISO 8601 datetime")
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a valid ISO 8601 datetime")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be a valid ISO 8601 datetime")
    return value


def _validate_evm_address(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _EVM_ADDRESS_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a valid EVM address")
    return value


def _validate_wallet_reference(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a valid EVM address or private key")
    if _EVM_ADDRESS_RE.fullmatch(value) or _PRIVATE_KEY_RE.fullmatch(value):
        return value
    raise ValueError(f"{field_name} must be a valid EVM address or private key")


class SignalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal["x", "farcaster", "gecko", "gmgn"]
    source_event_id: str
    observed_at: str
    raw_text: str
    author_handle: str | None = None
    context_url: str | None = None
    suggested_name: str | None = None
    suggested_symbol: str | None = None
    fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", mode="before")
    @classmethod
    def validate_observed_at(cls, value: Any) -> str:
        return _validate_iso_datetime(value, "observed_at")


class ScoredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    score: int
    decision: Literal["skip", "review", "priority_review"]
    reason_codes: list[str]
    recommended_platform: Literal["clanker"]
    review_priority: Literal["review", "priority_review"]


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    candidate_id: str
    status: Literal["pending", "approved", "rejected", "expired", "deploying"]
    created_at: str
    expires_at: str
    locked_by: str | None = None
    locked_at: str | None = None
    telegram_message_id: str | None = None

    @field_validator("created_at", "expires_at", "locked_at", mode="before")
    @classmethod
    def validate_review_timestamps(cls, value: Any, info) -> str | None:
        if value is None:
            return None
        return _validate_iso_datetime(value, info.field_name)


class DeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    platform: Literal["clanker"]
    signer_wallet: str
    token_name: str
    token_symbol: str
    image_uri: str
    tax_bps: int = Field(ge=0)
    tax_recipient: str
    token_admin_enabled: bool
    token_reward_enabled: bool
    token_admin: str
    fee_recipient: str
    clanker_fee_bps: int | None = Field(default=None, ge=0, le=10000)
    paired_fee_bps: int | None = Field(default=None, ge=0, le=10000)
    source: Literal["x", "farcaster", "gecko", "gmgn"] | None = None
    source_event_id: str | None = None
    context_url: str | None = None
    author_handle: str | None = None
    metadata_description: str | None = None
    raw_context_excerpt: str | None = None

    @field_validator("signer_wallet", mode="before")
    @classmethod
    def validate_signer_wallet(cls, value: Any, info) -> str:
        return _validate_wallet_reference(value, info.field_name)

    @field_validator("tax_recipient", "token_admin", "fee_recipient", mode="before")
    @classmethod
    def validate_addresses(cls, value: Any, info) -> str:
        return _validate_evm_address(value, info.field_name)


class DeployResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deploy_request_id: str
    status: Literal["deploy_success", "deploy_failed"]
    tx_hash: str | None = None
    contract_address: str | None = None
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None
    completed_at: str

    @field_validator("tx_hash", mode="before")
    @classmethod
    def validate_tx_hash(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _TX_HASH_RE.fullmatch(value):
            raise ValueError("tx_hash must be 0x followed by 64 hex characters")
        return value

    @field_validator("contract_address", mode="before")
    @classmethod
    def validate_contract_address(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _validate_evm_address(value, "contract_address")

    @field_validator("completed_at", mode="before")
    @classmethod
    def validate_completed_at(cls, value: Any) -> str:
        return _validate_iso_datetime(value, "completed_at")
