"""Tests for DeployPreparation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from clankandclaw.core.deploy_preparation import DeployPreparation, DeployPreparationError
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
    pinata.upload_json_metadata = AsyncMock(return_value="QmMetaHash")

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
    with pytest.raises(DeployPreparationError, match="Token extraction failed"):
        await prep._extract_token_identity(candidate)


# ── prepare image ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_image_uploads_and_returns_ipfs_uri(db, monkeypatch):
    prep, pinata, _ = make_preparation(db)

    async def fake_fetch(url: str) -> bytes:
        return b"fake-image-bytes"

    monkeypatch.setattr("clankandclaw.core.deploy_preparation.fetch_image_bytes", fake_fetch)

    candidate = make_candidate(metadata={"image_url": "https://example.com/logo.png"})
    uri = await prep._prepare_image(candidate)

    assert uri == "ipfs://QmImageHash"
    pinata.upload_file_bytes.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_image_raises_when_no_image_url(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={})
    with pytest.raises(DeployPreparationError, match="No image URL"):
        await prep._prepare_image(candidate)


# ── prepare metadata ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prepare_metadata_includes_author_when_present(db):
    prep, pinata, _ = make_preparation(db)
    candidate = make_candidate()
    candidate = candidate.model_copy(update={"author_handle": "alice"})

    await prep._prepare_metadata("Moon", "MOON", candidate, "ipfs://QmImg")

    call_kwargs = pinata.upload_json_metadata.call_args[0][0]
    trait_types = [a["trait_type"] for a in call_kwargs["attributes"]]
    assert "Author" in trait_types


@pytest.mark.asyncio
async def test_prepare_metadata_omits_author_when_absent(db):
    prep, pinata, _ = make_preparation(db)
    candidate = make_candidate()

    await prep._prepare_metadata("Moon", "MOON", candidate, "ipfs://QmImg")

    call_kwargs = pinata.upload_json_metadata.call_args[0][0]
    trait_types = [a["trait_type"] for a in call_kwargs["attributes"]]
    assert "Author" not in trait_types


# ── get_candidate_by_id ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_candidate_returns_none_for_missing_id(db):
    prep, _, _ = make_preparation(db)
    result = await prep.get_candidate_by_id("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_candidate_reconstructs_from_db(db):
    metadata = {"image_url": "https://example.com/img.png", "context_url": "https://x.com/1", "author_handle": "bob"}
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
async def test_prepare_deploy_request_returns_valid_deploy_request(db, monkeypatch):
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
    assert deploy_request.metadata_uri == "ipfs://QmMetaHash"
    assert deploy_request.candidate_id == candidate.id


@pytest.mark.asyncio
async def test_prepare_deploy_request_wraps_errors_as_deploy_preparation_error(db):
    prep, _, _ = make_preparation(db)
    candidate = make_candidate(metadata={})  # no image_url → will fail

    with pytest.raises(DeployPreparationError):
        await prep.prepare_deploy_request(candidate)
