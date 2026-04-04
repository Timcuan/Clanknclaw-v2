from clankandclaw.deployers.clanker import build_clanker_payload
from clankandclaw.models.token import DeployRequest


def test_build_clanker_payload_keeps_wallet_roles_separate():
    payload = build_clanker_payload(
        DeployRequest(
            candidate_id="sig-1",
            platform="clanker",
            signer_wallet="0x0000000000000000000000000000000000000003",
            token_name="Pepe",
            token_symbol="PEPE",
            image_uri="ipfs://image",
            metadata_uri="ipfs://meta",
            tax_bps=1000,
            tax_recipient="0x0000000000000000000000000000000000000004",
            token_admin_enabled=True,
            token_reward_enabled=True,
            token_admin="0x0000000000000000000000000000000000000001",
            fee_recipient="0x0000000000000000000000000000000000000002",
        )
    )
    assert payload["tokenAdmin"] == "0x0000000000000000000000000000000000000001"
    assert payload["rewards"]["recipients"][0]["recipient"] == "0x0000000000000000000000000000000000000002"
