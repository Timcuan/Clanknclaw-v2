"""Deploy preparation pipeline for approved candidates."""

import json
import logging
from datetime import datetime, timezone
from time import perf_counter

from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.models.token import DeployRequest, SignalCandidate
from clankandclaw.utils.extraction import extract_token_identity
from clankandclaw.utils.image_fetcher import fetch_image_bytes
from clankandclaw.utils.ipfs import PinataClient

logger = logging.getLogger(__name__)


class DeployPreparationError(Exception):
    """Error during deploy preparation."""


def _step_error(step: str, exc: Exception | str) -> DeployPreparationError:
    if isinstance(exc, Exception):
        return DeployPreparationError(f"{step}: {exc}")
    return DeployPreparationError(f"{step}: {exc}")


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
        logger.info("Preparing deploy request for candidate %s", candidate.id)

        try:
            extract_started = perf_counter()
            token_name, token_symbol = await self._extract_token_identity(candidate)
            logger.info(
                "deploy_prepare.extract_ms=%d candidate=%s",
                int((perf_counter() - extract_started) * 1000),
                candidate.id,
            )

            image_started = perf_counter()
            image_uri = await self._prepare_image(candidate)
            logger.info(
                "deploy_prepare.image_ms=%d candidate=%s",
                int((perf_counter() - image_started) * 1000),
                candidate.id,
            )

            deploy_request = DeployRequest(
                candidate_id=candidate.id,
                platform="clanker",
                signer_wallet=self.signer_wallet,
                token_name=token_name.strip(),
                token_symbol=token_symbol.strip().upper(),
                image_uri=image_uri,
                tax_bps=self.tax_bps,
                tax_recipient=self.fee_recipient,
                token_admin_enabled=True,
                token_reward_enabled=True,
                token_admin=self.token_admin,
                fee_recipient=self.fee_recipient,
            )

            preflight_started = perf_counter()
            await self.deployer.preflight(deploy_request)
            logger.info(
                "deploy_prepare.preflight_ms=%d candidate=%s",
                int((perf_counter() - preflight_started) * 1000),
                candidate.id,
            )

            return deploy_request

        except DeployPreparationError:
            raise
        except Exception as exc:
            logger.error("Deploy preparation failed for %s: %s", candidate.id, exc, exc_info=True)
            raise _step_error("prepare_deploy_request", exc) from exc

    async def _extract_token_identity(self, candidate: SignalCandidate) -> tuple[str, str]:
        """Extract token name and symbol from candidate."""
        if candidate.suggested_name and candidate.suggested_symbol:
            return candidate.suggested_name, candidate.suggested_symbol

        try:
            result = extract_token_identity(candidate.raw_text)
            return result.name, result.symbol
        except Exception as exc:
            raise _step_error("extract_identity", exc) from exc

    async def _prepare_image(self, candidate: SignalCandidate) -> str:
        """Fetch image and upload to IPFS."""
        image_url = candidate.metadata.get("image_url")
        if not image_url:
            raise _step_error("image_prepare", "missing image_url")

        try:
            image_bytes = await fetch_image_bytes(image_url)
            ipfs_hash = await self.pinata.upload_file_bytes(
                filename="token_image.png",
                content=image_bytes,
                content_type="image/png",
            )
            return f"ipfs://{ipfs_hash}"
        except Exception as exc:
            raise _step_error("image_prepare", exc) from exc

    async def get_candidate_by_id(self, candidate_id: str) -> SignalCandidate | None:
        """Retrieve a candidate from the database."""
        row = self.db.get_candidate(candidate_id)
        if not row:
            logger.warning("Candidate %s not found in database", candidate_id)
            return None

        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}

        return SignalCandidate(
            id=row["id"],
            source=row["source"],
            source_event_id=row["source_event_id"],
            observed_at=row["observed_at"] or datetime.now(timezone.utc).isoformat(),
            raw_text=row["raw_text"],
            fingerprint=row["fingerprint"],
            author_handle=metadata.get("author_handle"),
            context_url=metadata.get("context_url"),
            suggested_name=metadata.get("suggested_name"),
            suggested_symbol=metadata.get("suggested_symbol"),
            metadata=metadata,
        )
