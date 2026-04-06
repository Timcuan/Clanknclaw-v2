"""Tests for DeployPreparation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clankandclaw.core.deploy_preparation import (
    DeployPreparation,
    DeployPreparationError,
    _optimize_image_for_ipfs,
)
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.models.token import SignalCandidate


# ── helpers ──────────────────────────────────────────────────────────────────

def make_candidate(
    *,
    id: str = "x-1",
    raw_text: str = "deploy token Moon symbol MOON",
    suggested_name: str | None = None,
    suggested_symbol: str | None = None,
    metadata: dict | None = None,
) -> SignalCandidate:
    return SignalCandidate(
        id=id,
        source="x",
        source_event_id="tweet-1",
        observed_at="2026-04-05T10:00:00Z",
        raw_text=raw_text,
        fingerprint="fp-1",
        suggested_name=suggested_name,
        suggested_symbol=suggested_symbol,
        metadata={"image_url": "https://example.com/img.png"} if metadata is None else metadata,
    )


def make_preparation(db: DatabaseManager) -> tuple[DeployPreparation, MagicMock, MagicMock]:
    pinata = MagicMock()
    pinata.upload_file_bytes = AsyncMock(return_value="QmImageHash")

    deployer = MagicMock()
    deployer.preflight = AsyncMock(return_value=None)

    prep = DeployPreparation(
        db=db,
        pinata_client=pinata,
        deployer=deployer,
        signer_wallet="0x" + "a" * 40,
        token_admin="0x" + "b" * 40,
        fee_recipient="0x" + "c" * 40,
        tax_bps=1000,
    )
    return prep, pinata, deployer


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


# ── extract token identity ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_uses_suggested_values_when_present(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(suggested_name="Moon", suggested_symbol="MOON")
    name, symbol = await prep._extract_token_identity(candidate)
    assert name == "Moon"
    assert symbol == "MOON"


@pytest.mark.asyncio
async def test_extract_falls_back_to_regex(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(raw_text="deploy token Pepe symbol PEPE")
    name, symbol = await prep._extract_token_identity(candidate)
    assert name == "Pepe"
    assert symbol == "PEPE"


@pytest.mark.asyncio
async def test_extract_raises_deploy_preparation_error_when_extraction_fails(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(raw_text="nothing useful here")
    with pytest.raises(DeployPreparationError, match="extract_identity"):
        await prep._extract_token_identity(candidate)


# ── prepare image ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_image_uploads_and_returns_ipfs_uri(db, monkeypatch):
    prep, pinata, _ = make_preparation(db)

    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    candidate = make_candidate(metadata={"image_url": "https://example.com/logo.png"})
    uri = await prep._prepare_image(candidate, "Moon", "MOON")

    assert uri == "ipfs://QmImageHash"
    pinata.upload_file_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_image_skips_profile_image_and_uses_better_candidate(db, monkeypatch):
    prep, pinata, _ = make_preparation(db)

    async def fake_fetch(url: str) -> bytes:
        # Return valid bytes; URL scoring/plausibility should avoid profile_images URL.
        if "profile_images" in url:
            return b"first-image"
        return b"second-image"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(
        "clankandclaw.core.deploy_preparation._is_image_content_plausible",
        lambda raw, url, source: "profile_images" not in url,
    )

    candidate = make_candidate(
        metadata={
            "image_candidates": [
                "https://pbs.twimg.com/profile_images/abc/avatar.png",
                "https://cdn.example.com/moon-logo.png",
            ]
        }
    )
    uri = await prep._prepare_image(candidate, "Moon", "MOON")
    assert uri == "ipfs://QmImageHash"
    assert pinata.upload_file_bytes.await_count == 1


def test_optimize_image_for_ipfs_fallback_for_invalid_bytes():
    content, filename, content_type = _optimize_image_for_ipfs(b"not-an-image")
    assert content == b"not-an-image"
    assert filename == "token_image.png"
    assert content_type == "image/png"


def test_optimize_image_for_ipfs_optimizes_valid_image():
    image_module = pytest.importorskip("PIL.Image")
    from io import BytesIO
    Image = image_module
    img = Image.new("RGB", (2048, 2048), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    content, filename, content_type = _optimize_image_for_ipfs(raw)
    assert len(content) > 0
    assert filename in {"token_image.webp", "token_image.png"}
    assert content_type in {"image/webp", "image/png"}


@pytest.mark.asyncio
async def test_prepare_image_uses_placeholder_when_no_image_url(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={})
    uri = await prep._prepare_image(candidate, "Moon", "MOON")
    assert uri == "ipfs://QmImageHash"


# ── get_candidate_by_id ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_candidate_returns_none_for_missing_id(db):
    prep, _, _ = make_preparation(db)
    result = await prep.get_candidate_by_id("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_candidate_reconstructs_from_db(db):
    metadata = {
        "image_url": "https://example.com/img.png",
        "context_url": "https://x.com/1",
        "author_handle": "bob",
        "suggested_name": "Star",
        "suggested_symbol": "STAR",
    }
    db.save_candidate(
        "x-99", "x", "tweet-99", "fp-99",
        "deploy token Star symbol STAR",
        observed_at="2026-04-05T10:00:00Z",
        metadata=metadata,
    )
    prep, _, _ = make_preparation(db)
    candidate = await prep.get_candidate_by_id("x-99")

    assert candidate is not None
    assert candidate.id == "x-99"
    assert candidate.context_url == "https://x.com/1"
    assert candidate.author_handle == "bob"
    assert candidate.suggested_name == "Star"
    assert candidate.suggested_symbol == "STAR"
    assert candidate.metadata["image_url"] == "https://example.com/img.png"


@pytest.mark.asyncio
async def test_get_candidate_handles_corrupt_metadata_json(db):
    import sqlite3
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            "INSERT INTO signal_candidates (id, source, source_event_id, fingerprint, raw_text, observed_at, metadata_json) VALUES (?,?,?,?,?,?,?)",
            ("x-bad", "x", "tweet-bad", "fp-bad", "text", "2026-04-05T10:00:00Z", "{invalid json}"),
        )
    prep, _, _ = make_preparation(db)
    candidate = await prep.get_candidate_by_id("x-bad")
    assert candidate is not None
    assert candidate.metadata == {}


# ── prepare_deploy_request (end-to-end) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_deploy_request_returns_valid_deploy_request_without_metadata_uri(
    db, monkeypatch
):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    prep, _, _ = make_preparation(db)
    candidate = make_candidate(
        raw_text="deploy token Moon symbol MOON",
        metadata={"image_url": "https://example.com/img.png"},
    )

    deploy_request = await prep.prepare_deploy_request(candidate)

    assert deploy_request.token_name == "Moon"
    assert deploy_request.token_symbol == "MOON"
    assert deploy_request.image_uri == "ipfs://QmImageHash"
    assert deploy_request.candidate_id == candidate.id
    assert deploy_request.source == "x"
    assert deploy_request.source_event_id == "tweet-1"
    assert "Moon (MOON)" in (deploy_request.metadata_description or "")
    assert deploy_request.raw_context_excerpt is not None
    assert not hasattr(deploy_request, "metadata_uri")


@pytest.mark.asyncio
async def test_prepare_deploy_request_wraps_step_name_in_error_message(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    prep, pinata, _ = make_preparation(db)
    pinata.upload_file_bytes = AsyncMock(side_effect=RuntimeError("pinata down"))
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    with pytest.raises(DeployPreparationError, match="image_prepare"):
        await prep.prepare_deploy_request(candidate)


@pytest.mark.asyncio
async def test_prepare_deploy_request_normalizes_name_and_symbol(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    prep, _, _ = make_preparation(db)
    candidate = make_candidate(
        raw_text="launch token Moon$$ Coin symbol $mo-on",
        suggested_name="Moon$$   Coin",
        suggested_symbol="$mo-on",
        metadata={"image_url": "https://example.com/img.png"},
    )

    deploy_request = await prep.prepare_deploy_request(candidate)

    assert deploy_request.token_name == "Moon Coin"
    assert deploy_request.token_symbol == "MOON"


@pytest.mark.asyncio
async def test_prepare_deploy_request_applies_pool_fee_admin_reward_config(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    pinata = MagicMock()
    pinata.upload_file_bytes = AsyncMock(return_value="QmImageHash")
    deployer = MagicMock()
    deployer.preflight = AsyncMock(return_value=None)

    prep = DeployPreparation(
        db=db,
        pinata_client=pinata,
        deployer=deployer,
        signer_wallet="0x" + "a" * 40,
        token_admin="0x" + "b" * 40,
        fee_recipient="0x" + "c" * 40,
        tax_bps=1000,
        clanker_fee_bps=900,
        paired_fee_bps=1100,
        token_admin_enabled=False,
        token_reward_enabled=False,
    )
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    deploy_request = await prep.prepare_deploy_request(candidate)

    assert deploy_request.clanker_fee_bps == 900
    assert deploy_request.paired_fee_bps == 1100
    assert deploy_request.token_admin_enabled is False
    assert deploy_request.token_reward_enabled is False


@pytest.mark.asyncio
async def test_prepare_deploy_request_uses_runtime_wallet_overrides(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    db.set_runtime_setting("wallet.deployer_signer", "0x" + "d" * 64)
    db.set_runtime_setting("wallet.token_admin", "0x" + "e" * 40)
    db.set_runtime_setting("wallet.fee_recipient", "0x" + "f" * 40)

    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    deploy_request = await prep.prepare_deploy_request(candidate)

    assert deploy_request.signer_wallet == "0x" + "d" * 64
    assert deploy_request.token_admin == "0x" + "e" * 40
    assert deploy_request.fee_recipient == "0x" + "f" * 40
    assert deploy_request.tax_recipient == "0x" + "f" * 40


@pytest.mark.asyncio
async def test_prepare_deploy_request_fails_on_invalid_runtime_wallet_override(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)
    db.set_runtime_setting("wallet.token_admin", "not-an-address")

    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    with pytest.raises(DeployPreparationError, match="wallet_runtime"):
        await prep.prepare_deploy_request(candidate)


@pytest.mark.asyncio
async def test_prepare_deploy_request_logs_step_timings(db, monkeypatch, caplog):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    with caplog.at_level("INFO"):
        await prep.prepare_deploy_request(candidate)

    assert any("deploy_prepare.extract_ms=" in record.message for record in caplog.records)
    assert any("deploy_prepare.image_ms=" in record.message for record in caplog.records)
    assert any("deploy_prepare.preflight_ms=" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_prepare_deploy_request_wraps_errors_as_deploy_preparation_error(db, monkeypatch):
    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    prep, pinata, _ = make_preparation(db)
    pinata.upload_file_bytes = AsyncMock(side_effect=RuntimeError("pinata upload failed"))
    candidate = make_candidate(metadata={"image_url": "https://example.com/img.png"})

    with pytest.raises(DeployPreparationError):
        await prep.prepare_deploy_request(candidate)
