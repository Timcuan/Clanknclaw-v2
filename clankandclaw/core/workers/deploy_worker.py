"""Deploy worker for handling deployment preparation and execution."""

import asyncio
import logging
import uuid
from time import perf_counter
from typing import Any

from clankandclaw.core.deploy_preparation import DeployPreparation, DeployPreparationError
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.models.token import SignalCandidate
from clankandclaw.utils.ipfs import PinataClient

logger = logging.getLogger(__name__)


class DeployWorker:
    """Worker that handles deploy preparation and execution."""

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
        self.preparation = DeployPreparation(
            db=db,
            pinata_client=pinata_client,
            deployer=deployer,
            signer_wallet=signer_wallet,
            token_admin=token_admin,
            fee_recipient=fee_recipient,
            tax_bps=tax_bps,
        )
        self.deployer = deployer
        self._telegram_worker: Any = None  # Will be set by supervisor
        self._running = False

    def set_telegram_worker(self, telegram_worker: Any) -> None:
        """Set the telegram worker for sending notifications."""
        self._telegram_worker = telegram_worker

    async def start(self) -> None:
        """Start the deploy worker (no background task needed)."""
        self._running = True
        logger.info("Deploy worker started")

    async def stop(self) -> None:
        """Stop the deploy worker."""
        self._running = False
        logger.info("Deploy worker stopped")

    async def prepare_and_deploy(self, candidate_id: str) -> None:
        """Prepare and execute deployment for an approved candidate."""
        if not self._running:
            logger.warning("Deploy worker not running")
            return

        logger.info("Starting deploy process for %s", candidate_id)

        try:
            lookup_started = perf_counter()
            candidate = await self._get_candidate(candidate_id)
            logger.info(
                "deploy_worker.lookup_ms=%d candidate=%s",
                int((perf_counter() - lookup_started) * 1000),
                candidate_id,
            )
            if not candidate:
                raise DeployPreparationError(f"lookup_candidate: Candidate {candidate_id} not found")

            prepare_started = perf_counter()
            deploy_request = await self.preparation.prepare_deploy_request(candidate)
            logger.info(
                "deploy_worker.prepare_ms=%d candidate=%s",
                int((perf_counter() - prepare_started) * 1000),
                candidate_id,
            )

            deploy_started = perf_counter()
            deploy_result = await self.deployer.deploy(deploy_request)
            logger.info(
                "deploy_worker.deploy_ms=%d candidate=%s",
                int((perf_counter() - deploy_started) * 1000),
                candidate_id,
            )

            self.db.save_deployment_result(
                result_id=str(uuid.uuid4()),
                candidate_id=candidate_id,
                status=deploy_result.status,
                deployed_at=deploy_result.completed_at,
                tx_hash=deploy_result.tx_hash,
                contract_address=deploy_result.contract_address,
                error_code=deploy_result.error_code,
                error_message=deploy_result.error_message,
                latency_ms=deploy_result.latency_ms,
            )

            if deploy_result.status == "deploy_success":
                if self._telegram_worker:
                    await self._telegram_worker.send_deploy_success(
                        candidate_id,
                        deploy_result.tx_hash or "unknown",
                        deploy_result.contract_address or "unknown",
                    )
            else:
                logger.error(
                    f"Deploy failed for {candidate_id}: "
                    f"{deploy_result.error_code} - {deploy_result.error_message}"
                )
                if self._telegram_worker:
                    await self._telegram_worker.send_deploy_failure(
                        candidate_id,
                        deploy_result.error_code or "unknown",
                        deploy_result.error_message or "Unknown error",
                    )

        except DeployPreparationError as exc:
            logger.error("Deploy preparation failed for %s: %s", candidate_id, exc)
            if self._telegram_worker:
                await self._telegram_worker.send_deploy_failure(
                    candidate_id,
                    "preparation_failed",
                    str(exc),
                )
        except Exception as exc:
            logger.error("Deploy failed for %s: %s", candidate_id, exc, exc_info=True)
            if self._telegram_worker:
                await self._telegram_worker.send_deploy_failure(
                    candidate_id,
                    "deploy_failed",
                    str(exc),
                )

    async def _get_candidate(self, candidate_id: str) -> SignalCandidate | None:
        """Get candidate from database."""
        return await self.preparation.get_candidate_by_id(candidate_id)
