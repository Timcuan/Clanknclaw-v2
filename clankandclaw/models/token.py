from typing import Any, Literal

from pydantic import BaseModel, Field


class SignalCandidate(BaseModel):
    id: str
    source: Literal["x", "gmgn"]
    source_event_id: str
    observed_at: str
    raw_text: str
    author_handle: str | None = None
    context_url: str | None = None
    suggested_name: str | None = None
    suggested_symbol: str | None = None
    fingerprint: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoredCandidate(BaseModel):
    candidate_id: str
    score: int
    decision: Literal["skip", "review", "priority_review"]
    reason_codes: list[str]
    recommended_platform: Literal["clanker"]
    review_priority: Literal["review", "priority_review"]


class ReviewItem(BaseModel):
    id: str
    candidate_id: str
    status: Literal["pending", "approved", "rejected", "expired", "deploying"]
    created_at: str
    expires_at: str
    locked_by: str | None = None
    locked_at: str | None = None
    telegram_message_id: str | None = None


class DeployRequest(BaseModel):
    candidate_id: str
    platform: Literal["clanker"]
    signer_wallet: str
    token_name: str
    token_symbol: str
    image_uri: str
    metadata_uri: str
    tax_bps: int
    tax_recipient: str
    token_admin_enabled: bool
    token_reward_enabled: bool
    token_admin: str
    fee_recipient: str


class DeployResult(BaseModel):
    deploy_request_id: str
    status: Literal["deploy_success", "deploy_failed"]
    tx_hash: str | None = None
    contract_address: str | None = None
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None
