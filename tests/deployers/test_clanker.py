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
        tax_bps=1000,
        tax_recipient="0x0000000000000000000000000000000000000004",
        token_admin_enabled=token_admin_enabled,
        token_reward_enabled=token_reward_enabled,
        token_admin="0x0000000000000000000000000000000000000001",
        fee_recipient="0x0000000000000000000000000000000000000002",
        source="x",
        source_event_id="tweet-1",
        context_url="https://x.com/alice/status/1",
        author_handle="alice",
        metadata_description="Pepe (PEPE) is a Base community token derived from a social signal.",
        raw_context_excerpt="meme narrative catching attention",
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
    assert payload["rewards"]["recipients"][0]["recipient"] == "0x0000000000000000000000000000000000000001"
    assert payload["rewards"]["recipients"][0]["bps"] == 10
    assert payload["rewards"]["recipients"][1]["recipient"] == "0x0000000000000000000000000000000000000002"
    assert payload["rewards"]["recipients"][1]["bps"] == 9990


def test_build_clanker_payload_reward_split_sums_to_100_percent():
    payload = build_clanker_payload(make_deploy_request())
    total = sum(item["bps"] for item in payload["rewards"]["recipients"])
    assert total == 10000


def test_build_clanker_payload_omits_token_admin_when_disabled():
    payload = build_clanker_payload(make_deploy_request(token_admin_enabled=False))

    assert "tokenAdmin" not in payload


def test_build_clanker_payload_omits_rewards_when_disabled():
    payload = build_clanker_payload(make_deploy_request(token_reward_enabled=False))

    assert "rewards" not in payload


def test_build_clanker_payload_metadata_shape():
    payload = build_clanker_payload(make_deploy_request())

    assert "description" in payload["metadata"]
    assert "external_url" in payload["metadata"]
    assert "auditUrls" not in payload["metadata"]
    assert "vault" not in payload
    assert "devBuy" not in payload


def test_build_clanker_payload_has_required_sdk_fields():
    payload = build_clanker_payload(make_deploy_request())

    for field in ("name", "symbol", "image", "tokenAdmin", "context", "pool", "fees"):
        assert field in payload, f"Missing required field: {field}"
    assert payload["context"]["platform"] == "x"
    assert payload["context"]["authorHandle"] == "alice"


def test_build_clanker_payload_uses_hardcoded_pool_and_configurable_fee_split():
    req = make_deploy_request()
    req.clanker_fee_bps = 900
    req.paired_fee_bps = 1100

    payload = build_clanker_payload(req)
    assert payload["pool"]["pairedToken"] == "0x4200000000000000000000000000000000000006"
    assert payload["startingMarketCapEth"] == 10.0
    assert payload["fees"]["clankerFee"] == 900
    assert payload["fees"]["pairedFee"] == 1100


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


def test_parse_sdk_output_success_requires_tx_hash_and_contract():
    import json
    stdout = json.dumps({"status": "success", "txHash": "not-a-tx", "contractAddress": "0x123"})
    result = parse_sdk_output(stdout, "", 0, "sig-1")
    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_sdk_output"


def test_parse_sdk_output_sdk_not_installed():
    stderr = "node:internal/modules/esm/resolve:265\nERR_MODULE_NOT_FOUND: Cannot find package 'clanker-sdk'"
    result = parse_sdk_output("", stderr, 1, "sig-1")

    assert result.status == "deploy_failed"
    assert result.error_code == "sdk_not_installed"


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
async def test_preflight_rejects_invalid_symbol_characters():
    bad_request = make_deploy_request()
    bad_request.token_symbol = "PE-PE"
    deployer = ClankerDeployer()

    result = await deployer.deploy(bad_request)
    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_config"
    assert "A-Z0-9" in (result.error_message or "")


@pytest.mark.asyncio
async def test_preflight_rejects_invalid_name_characters():
    bad_request = make_deploy_request()
    bad_request.token_name = "<Pepe>"
    deployer = ClankerDeployer()

    result = await deployer.deploy(bad_request)
    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_config"
    assert "unsupported characters" in (result.error_message or "")


@pytest.mark.asyncio
async def test_preflight_rejects_reward_enabled_without_admin():
    bad_request = make_deploy_request(token_admin_enabled=False, token_reward_enabled=True)
    deployer = ClankerDeployer()
    result = await deployer.deploy(bad_request)
    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_config"
    assert "requires token_admin_enabled" in (result.error_message or "")


@pytest.mark.asyncio
async def test_preflight_rejects_zero_fee_recipient():
    bad_request = make_deploy_request()
    bad_request.fee_recipient = "0x0000000000000000000000000000000000000000"
    deployer = ClankerDeployer()
    result = await deployer.deploy(bad_request)
    assert result.status == "deploy_failed"
    assert result.error_code == "invalid_config"
    assert "must not be zero address" in (result.error_message or "")


@pytest.mark.asyncio
async def test_execute_with_sdk_fails_fast_when_node_modules_missing(tmp_path):
    deployer = ClankerDeployer(node_modules_path=tmp_path / "missing_node_modules")
    deploy_request = make_deploy_request()
    config = await deployer.prepare(deploy_request)

    result = await deployer._execute_with_sdk(deploy_request, config)
    assert result.status == "deploy_failed"
    assert result.error_code == "sdk_not_installed"


@pytest.mark.asyncio
async def test_deploy_uses_custom_execute_hook():
    """deploy() calls the injected execute hook and returns its result."""
    from clankandclaw.models.token import DeployResult
    from datetime import datetime, timezone

    async def custom_execute(config, req):
        return DeployResult(
            deploy_request_id=req.candidate_id,
            status="deploy_success",
            tx_hash="0x" + "c" * 64,
            contract_address="0x" + "d" * 40,
            latency_ms=10,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

    deployer = ClankerDeployer(execute=custom_execute)
    result = await deployer.deploy(make_deploy_request())

    assert result.status == "deploy_success"
    assert result.tx_hash == "0x" + "c" * 64
