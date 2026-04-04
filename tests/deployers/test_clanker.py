import pytest

from clankandclaw.deployers.clanker import ClankerDeployer, build_clanker_payload
from clankandclaw.models.token import DeployRequest


def make_deploy_request(
    *,
    token_admin_enabled: bool = True,
    token_reward_enabled: bool = True,
) -> DeployRequest:
    return DeployRequest(
        candidate_id="sig-1",
        platform="clanker",
        signer_wallet="0x0000000000000000000000000000000000000003",
        token_name="Pepe",
        token_symbol="PEPE",
        image_uri="ipfs://image",
        metadata_uri="ipfs://meta",
        tax_bps=1000,
        tax_recipient="0x0000000000000000000000000000000000000004",
        token_admin_enabled=token_admin_enabled,
        token_reward_enabled=token_reward_enabled,
        token_admin="0x0000000000000000000000000000000000000001",
        fee_recipient="0x0000000000000000000000000000000000000002",
    )


@pytest.mark.asyncio
async def test_clanker_deployer_prepare_and_preflight_are_usable():
    deploy_request = make_deploy_request()
    deployer = ClankerDeployer()

    payload = await deployer.prepare(deploy_request)
    preflight = await deployer.preflight(deploy_request)

    assert payload["name"] == deploy_request.token_name
    assert payload["symbol"] == deploy_request.token_symbol
    assert preflight is None


def test_build_clanker_payload_keeps_wallet_roles_separate():
    payload = build_clanker_payload(make_deploy_request())

    assert payload["tokenAdmin"] == "0x0000000000000000000000000000000000000001"
    assert payload["rewards"]["recipients"][0]["recipient"] == "0x0000000000000000000000000000000000000002"


def test_build_clanker_payload_omits_token_admin_when_disabled():
    payload = build_clanker_payload(make_deploy_request(token_admin_enabled=False))

    assert "tokenAdmin" not in payload


def test_build_clanker_payload_omits_rewards_when_disabled():
    payload = build_clanker_payload(make_deploy_request(token_reward_enabled=False))

    assert "rewards" not in payload
