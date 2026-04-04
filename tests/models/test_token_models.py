from clankandclaw.models.token import DeployRequest, SignalCandidate


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
    deploy_request = DeployRequest(
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
    assert deploy_request.signer_wallet.endswith("0003")
