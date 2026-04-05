"""Supervisor for managing async workers."""

import asyncio
import logging
import signal
from typing import Any

from clankandclaw.config import AppConfig
from clankandclaw.core.workers.deploy_worker import DeployWorker
from clankandclaw.core.workers.gmgn_detector_worker import GMGNDetectorWorker
from clankandclaw.core.workers.telegram_worker import TelegramWorker
from clankandclaw.core.workers.x_detector_worker import XDetectorWorker
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.utils.ipfs import PinataClient

logger = logging.getLogger(__name__)


class Supervisor:
    """Manages the lifecycle of all async workers."""

    def __init__(
        self,
        config: AppConfig,
        db: DatabaseManager,
    ):
        self.config = config
        self.db = db
        self._workers: dict[str, Any] = {}
        self._running = False
        self._shutdown_event = asyncio.Event()

    def worker_names(self) -> list[str]:
        """Return list of worker names (for testing compatibility)."""
        return list(self._workers.keys())

    async def start(self) -> None:
        """Start all workers."""
        if self._running:
            logger.warning("Supervisor already running")
            return

        self._running = True
        logger.info("Starting supervisor")

        # Initialize dependencies
        try:
            pinata = PinataClient()
        except ValueError as exc:
            logger.warning(f"Pinata client not configured: {exc}")
            pinata = None  # type: ignore

        deployer = ClankerDeployer(
            rpc_url=self.config.deployment.base_rpc_url or None,
            executor_path=__import__("pathlib").Path(self.config.deployment.executor_path) if self.config.deployment.executor_path else None,
            node_script_path=__import__("pathlib").Path(self.config.deployment.node_script_path) if self.config.deployment.node_script_path else None,
        )

        # Initialize Telegram worker
        telegram = TelegramWorker(
            self.db,
            review_expiry_seconds=self.config.app.review_expiry_seconds,
            bot_token=self.config.telegram.bot_token or None,
            chat_id=self.config.telegram.chat_id or None,
        )
        self._workers["telegram"] = telegram

        # Initialize deploy worker if pinata is available
        if pinata:
            deploy = DeployWorker(
                db=self.db,
                pinata_client=pinata,
                deployer=deployer,
                signer_wallet=self.config.wallets.deployer_signer_private_key,
                token_admin=self.config.wallets.token_admin,
                fee_recipient=self.config.wallets.fee_recipient,
                tax_bps=self.config.deployment.tax_bps,
            )
            self._workers["deploy"] = deploy
            deploy.set_telegram_worker(telegram)
            telegram.set_deploy_preparation(deploy)
        else:
            logger.warning("Deploy worker disabled (Pinata not configured)")

        # Initialize detector workers (only if enabled)
        if self.config.x_detector.enabled:
            x_detector = XDetectorWorker(
                self.db,
                poll_interval=self.config.x_detector.poll_interval,
                keywords=self.config.x_detector.keywords,
                max_results=self.config.x_detector.max_results,
            )
            x_detector.set_telegram_worker(telegram)
            self._workers["x_detector"] = x_detector
        else:
            logger.info("X detector disabled by config")

        if self.config.gmgn_detector.enabled:
            gmgn_detector = GMGNDetectorWorker(
                self.db,
                poll_interval=self.config.gmgn_detector.poll_interval,
                api_url=self.config.gmgn_detector.api_url,
                max_results=self.config.gmgn_detector.max_results,
            )
            gmgn_detector.set_telegram_worker(telegram)
            self._workers["gmgn_detector"] = gmgn_detector
        else:
            logger.info("GMGN detector disabled by config")

        # Start all workers
        for name, worker in self._workers.items():
            try:
                await worker.start()
                logger.info(f"Started worker: {name}")
            except Exception as exc:
                logger.error(f"Failed to start worker {name}: {exc}", exc_info=True)

        # Setup signal handlers
        self._setup_signal_handlers()

        logger.info("Supervisor started with workers: %s", list(self._workers.keys()))

    async def stop(self) -> None:
        """Stop all workers."""
        if not self._running:
            return

        logger.info("Stopping supervisor")
        self._running = False

        # Stop all workers
        for name, worker in self._workers.items():
            try:
                await worker.stop()
                logger.info(f"Stopped worker: {name}")
            except Exception as exc:
                logger.error(f"Error stopping worker {name}: {exc}", exc_info=True)

        self._workers.clear()
        logger.info("Supervisor stopped")

    async def run(self) -> None:
        """Run the supervisor until shutdown signal."""
        await self.start()
        
        try:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Supervisor cancelled")
        finally:
            await self.stop()

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        def signal_handler(sig: signal.Signals) -> None:
            logger.info(f"Received signal {sig.name}, initiating shutdown")
            self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
