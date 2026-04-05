import pytest

from clankandclaw.deployers.clanker import ClankerDeployer, build_clanker_payload, parse_sdk_output
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


# --- parse_sdk_output ---

def test_parse_sdk_output_success():
    import json
    stdout = json.dumps({"status": "success", "txHash": "0x" + "a" * 64, "contractAddress": "0x" + "b" * 40})
    result = parse_sdk_output(stdout, "", 0, "sig-1")

    assert result.status == "deploy_success"
    assert result.tx_hash == "0x" + "a" * 64
    assert result.contract_address == "0x" + "b" * 40


def test_parse_sdk_output_error_json():
    import json
    stderr = json.dumps({"status": "error", "errorCode": "sdk_error", "errorMessage": "something failed"})
    result = parse_sdk_output("", stderr, 1, "sig-1")

    assert result.status == "deploy_failed"
    assert result.error_code == "sdk_error"
    assert result.error_message == "something failed"


def test_parse_sdk_output_nonzero_exit_no_json():
    result = parse_sdk_output("", "plain error text", 1, "sig-1")

    assert result.status == "deploy_failed"
    assert result.error_code == "subprocess_failed"
    assert "plain error text" in result.error_message


def test_parse_sdk_output_malformed_json():
    result = parse_sdk_output("not-json", "", 0, "sig-1")

    assert result.status == "deploy_failed"
    assert result.error_code == "parse_error"


def test_parse_sdk_output_never_raises():
    """parse_sdk_output must not raise for any input combination."""
    for stdout, stderr, code in [
        ("", "", 0),
        ("garbage", "garbage", 99),
        ("{}", "{}", 0),
        ('{"status":"success"}', "", 0),  # missing fields
    ]:
        result = parse_sdk_output(stdout, stderr, code, "sig-x")
        assert result is not None
        assert result.deploy_request_id == "sig-x"


# --- deploy() error paths ---

@pytest.mark.asyncio
async def test_deploy_returns_failed_when_sdk_not_available():
    """deploy() returns deploy_failed (not raises) when Node.js is absent."""
    deployer = ClankerDeployer(rpc_url="https://mainnet.base.org")
    deployer._sdk_available = False

    result = await deployer.deploy(make_deploy_request())

    assert result.status == "deploy_failed"
    assert result.error_code == "sdk_not_available"


@pytest.mark.asyncio
async def test_deploy_returns_failed_on_preflight_error():
    """deploy() returns deploy_failed when preflight raises ValueError."""
    bad_request = make_deploy_request()
    bad_request.token_symbol = "lowercase"  # violates uppercase rule

    deployer = ClankerDeployer()
    result = await deployer.deploy(bad_request)

    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_config"


@pytest.mark.asyncio
async def test_deploy_uses_custom_executor():
    """deploy() calls the injected executor and returns its result."""
    from clankandclaw.models.token import DeployResult
    from datetime import datetime, timezone

    async def fake_executor(config, req):
        return DeployResult(
            deploy_request_id=req.candidate_id,
            status="deploy_success",
            tx_hash="0x" + "c" * 64,
            contract_address="0x" + "d" * 40,
            latency_ms=10,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    deployer = ClankerDeployer(execute=fake_executor)
    result = await deployer.deploy(make_deploy_request())

    assert result.status == "deploy_success"
    assert result.tx_hash == "0x" + "c" * 64
