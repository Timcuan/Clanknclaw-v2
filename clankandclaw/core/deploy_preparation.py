"""Deploy preparation pipeline for approved candidates."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.models.token import DeployRequest, SignalCandidate
from clankandclaw.utils.extraction import extract_token_identity
from clankandclaw.utils.image_fetcher import fetch_image_bytes
from clankandclaw.utils.ipfs import PinataClient

logger = logging.getLogger(__name__)


class DeployPreparationError(Exception):
    """Error during deploy preparation."""
    pass


class DeployPreparation:
    """Handles preparation of approved candidates for deployment."""

    def __init__(
        self,
        db: DatabaseManager,
        pinata_client: PinataClient,
        deployer: ClankerDeployer,
        signer_wallet: str,
        token_admin: str,
        fee_recipient: str,
        tax_bps: int = 1000,
    ):
        self.db = db
        self.pinata = pinata_client
        self.deployer = deployer
        self.signer_wallet = signer_wallet
        self.token_admin = token_admin
        self.fee_recipient = fee_recipient
        self.tax_bps = tax_bps

    async def prepare_deploy_request(
        self,
        candidate: SignalCandidate,
    ) -> DeployRequest:
        """Prepare a deploy request from an approved candidate."""
        logger.info(f"Preparing deploy request for candidate {candidate.id}")

        try:
            # Step 1: Extract token identity
            token_name, token_symbol = await self._extract_token_identity(candidate)
            logger.info(f"Extracted: {token_name} ({token_symbol})")

            # Step 2: Fetch and upload image
            image_uri = await self._prepare_image(candidate)
            logger.info(f"Image uploaded: {image_uri}")

            # Step 3: Build and upload metadata
            metadata_uri = await self._prepare_metadata(
                token_name, token_symbol, candidate, image_uri
            )
            logger.info(f"Metadata uploaded: {metadata_uri}")

            # Step 4: Build deploy request
            deploy_request = DeployRequest(
                candidate_id=candidate.id,
                platform="clanker",
                signer_wallet=self.signer_wallet,
                token_name=token_name,
                token_symbol=token_symbol,
                image_uri=image_uri,
                metadata_uri=metadata_uri,
                tax_bps=self.tax_bps,
                tax_recipient=self.fee_recipient,
                token_admin_enabled=True,
                token_reward_enabled=True,
                token_admin=self.token_admin,
                fee_recipient=self.fee_recipient,
            )

            # Step 5: Run deployer preflight checks
            await self.deployer.preflight(deploy_request)
            logger.info(f"Preflight checks passed for {candidate.id}")

            return deploy_request

        except Exception as exc:
            logger.error(f"Deploy preparation failed for {candidate.id}: {exc}", exc_info=True)
            raise DeployPreparationError(f"Preparation failed: {exc}") from exc

    async def _extract_token_identity(self, candidate: SignalCandidate) -> tuple[str, str]:
        """Extract token name and symbol from candidate."""
        # Try using suggested values first
        if candidate.suggested_name and candidate.suggested_symbol:
            logger.info("Using suggested token identity")
            return candidate.suggested_name, candidate.suggested_symbol

        # Fall back to extraction
        try:
            result = extract_token_identity(candidate.raw_text)
            return result.name, result.symbol
        except Exception as exc:
            raise DeployPreparationError(f"Token extraction failed: {exc}") from exc

    async def _prepare_image(self, candidate: SignalCandidate) -> str:
        """Fetch image and upload to IPFS."""
        # Try to get image URL from metadata
        image_url = candidate.metadata.get("image_url")
        
        if not image_url:
            # Try to extract from context_url or use a default
            # For MVP, we'll require image_url in metadata
            raise DeployPreparationError("No image URL found in candidate metadata")

        try:
            # Fetch image
            logger.info(f"Fetching image from {image_url}")
            image_bytes = await fetch_image_bytes(image_url)
            logger.info(f"Fetched {len(image_bytes)} bytes")

            # Upload to IPFS
            ipfs_hash = await self.pinata.upload_file_bytes(
                filename="token_image.png",
                content=image_bytes,
                content_type="image/png",
            )

            return f"ipfs://{ipfs_hash}"

        except Exception as exc:
            raise DeployPreparationError(f"Image preparation failed: {exc}") from exc

    async def _prepare_metadata(
        self,
        token_name: str,
        token_symbol: str,
        candidate: SignalCandidate,
        image_uri: str,
    ) -> str:
        """Build and upload metadata to IPFS."""
        try:
            # Build metadata JSON
            metadata: dict[str, Any] = {
                "name": token_name,
                "symbol": token_symbol,
                "image": image_uri,
                "description": f"Token deployed via Clank&Claw from {candidate.source}",
                "external_url": candidate.context_url or "",
                "attributes": [
                    {"trait_type": "Source", "value": candidate.source},
                    {"trait_type": "Candidate ID", "value": candidate.id},
                    {
                        "trait_type": "Deployed At",
                        "value": datetime.now(timezone.utc).isoformat(),
                    },
                ],
            }

            # Add author if available
            if candidate.author_handle:
                metadata["attributes"].append(
                    {"trait_type": "Author", "value": candidate.author_handle}
                )

            # Upload to IPFS
            ipfs_hash = await self.pinata.upload_json_metadata(metadata)

            return f"ipfs://{ipfs_hash}"

        except Exception as exc:
            raise DeployPreparationError(f"Metadata preparation failed: {exc}") from exc

    async def get_candidate_by_id(self, candidate_id: str) -> SignalCandidate | None:
        """Retrieve a candidate from the database."""
        row = self.db.get_candidate(candidate_id)
        if not row:
            logger.warning(f"Candidate {candidate_id} not found in database")
            return None
        
        # Reconstruct SignalCandidate from database row
        observed_at = row["observed_at"] or datetime.now(timezone.utc).isoformat()
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        return SignalCandidate(
            id=row["id"],
            source=row["source"],
            source_event_id=row["source_event_id"],
            observed_at=observed_at,
            raw_text=row["raw_text"],
            fingerprint=row["fingerprint"],
            author_handle=None,
            context_url=None,
            suggested_name=None,
            suggested_symbol=None,
            metadata=metadata,
        )
