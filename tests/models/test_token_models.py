from datetime import datetime

import pytest
from pydantic import ValidationError

from clankandclaw.models.token import (
    DeployRequest,
    DeployResult,
    ReviewItem,
    ScoredCandidate,
    SignalCandidate,
)


def _valid_deploy_request_data() -> dict[str, object]:
    return {
        "candidate_id": "sig-1",
        "platform": "clanker",
        "signer_wallet": "0x0000000000000000000000000000000000000003",
        "token_name": "Pepe",
        "token_symbol": "PEPE",
        "image_uri": "ipfs://image",
        "metadata_uri": "ipfs://meta",
        "tax_bps": 1000,
        "tax_recipient": "0x0000000000000000000000000000000000000004",
        "token_admin_enabled": True,
        "token_reward_enabled": True,
        "token_admin": "0x0000000000000000000000000000000000000001",
        "fee_recipient": "0x0000000000000000000000000000000000000002",
    }


def test_signal_candidate_has_required_fields():
    candidate = SignalCandidate(
        id="sig-1",
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-04T00:00:00Z",
        raw_text="deploy PEPE",
        author_handle="alice",
        context_url="https://x.example/1",
        suggested_name="Pepe",
        suggested_symbol="PEPE",
        fingerprint="fp-1",
        metadata={},
    )
    assert candidate.source == "x"


def test_deploy_request_separates_wallet_roles():
    deploy_request = DeployRequest(**_valid_deploy_request_data())
    assert len(
        {
            deploy_request.signer_wallet,
            deploy_request.tax_recipient,
            deploy_request.token_admin,
            deploy_request.fee_recipient,
        }
    ) == 4
    assert deploy_request.signer_wallet.endswith("0003")


def test_model_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SignalCandidate(
            id="sig-1",
            source="x",
            source_event_id="tweet-1",
            observed_at="2026-04-04T00:00:00Z",
            raw_text="deploy PEPE",
            author_handle="alice",
            context_url="https://x.example/1",
            suggested_name="Pepe",
            suggested_symbol="PEPE",
            fingerprint="fp-1",
            metadata={},
            extra_field="nope",
        )


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("signer_wallet", "not-an-address"),
        ("tax_recipient", "0x123"),
        ("token_admin", "0x00000000000000000000000000000000000000zz"),
        ("fee_recipient", "0x"),
    ],
)
def test_deploy_request_rejects_invalid_address(field_name: str, field_value: str):
    data = _valid_deploy_request_data()
    data[field_name] = field_value

    with pytest.raises(ValidationError):
        DeployRequest(**data)


@pytest.mark.parametrize(
    ("model_factory", "field_name"),
    [
        (
            lambda: SignalCandidate(
                id="sig-1",
                source="x",
                source_event_id="tweet-1",
                observed_at="not-a-timestamp",
                raw_text="deploy PEPE",
                author_handle="alice",
                context_url="https://x.example/1",
                suggested_name="Pepe",
                suggested_symbol="PEPE",
                fingerprint="fp-1",
                metadata={},
            ),
            "observed_at",
        ),
        (
            lambda: ReviewItem(
                id="review-1",
                candidate_id="sig-1",
                status="pending",
                created_at="2026-04-04T00:00:00Z",
                expires_at="bad-time",
                locked_by=None,
                locked_at=None,
                telegram_message_id=None,
            ),
            "expires_at",
        ),
        (
            lambda: DeployResult(
                deploy_request_id="deploy-1",
                status="deploy_success",
                tx_hash="0x" + "0" * 64,
                contract_address="0x" + "0" * 39 + "z",
                latency_ms=42,
                error_code=None,
                error_message=None,
                completed_at="2026-04-04T00:00:00Z",
            ),
            "contract_address",
        ),
    ],
)
def test_models_reject_invalid_timestamps_or_addresses(model_factory, field_name: str):
    with pytest.raises(ValidationError):
        model_factory()


def test_deploy_request_rejects_negative_tax_bps():
    data = _valid_deploy_request_data()
    data["tax_bps"] = -1

    with pytest.raises(ValidationError):
        DeployRequest(**data)


def test_minimal_model_coverage_for_scored_candidate_review_item_and_deploy_result():
    scored_candidate = ScoredCandidate(
        candidate_id="sig-1",
        score=87,
        decision="priority_review",
        reason_codes=["high_social_signal"],
        recommended_platform="clanker",
        review_priority="priority_review",
    )
    review_item = ReviewItem(
        id="review-1",
        candidate_id=scored_candidate.candidate_id,
        status="pending",
        created_at="2026-04-04T00:00:00Z",
        expires_at="2026-04-04T00:15:00Z",
        locked_by=None,
        locked_at=None,
        telegram_message_id="123",
    )
    deploy_result = DeployResult(
        deploy_request_id="deploy-1",
        status="deploy_success",
        tx_hash="0x" + "0" * 64,
        contract_address="0x" + "0" * 40,
        latency_ms=42,
        error_code=None,
        error_message=None,
        completed_at=datetime.fromisoformat("2026-04-04T00:00:00+00:00").isoformat(),
    )

    assert scored_candidate.review_priority == "priority_review"
    assert review_item.status == "pending"
    assert deploy_result.status == "deploy_success"
