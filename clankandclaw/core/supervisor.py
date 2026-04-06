"""Supervisor for managing async workers."""

import asyncio
import functools
import logging
import signal
from typing import Any

from clankandclaw.config import AppConfig
from clankandclaw.core.workers.deploy_worker import DeployWorker
from clankandclaw.core.workers.farcaster_detector_worker import FarcasterDetectorWorker
from clankandclaw.core.workers.gecko_detector_worker import GeckoDetectorWorker
from clankandclaw.core.workers.telegram_worker import TelegramWorker
from clankandclaw.core.workers.x_detector_worker import XDetectorWorker
from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.rewards.claimer import ClankerRewardsClaimer
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
        self._cleanup_task: asyncio.Task[None] | None = None

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
            node_modules_path=__import__("pathlib").Path(self.config.deployment.clanker_node_modules_path) if self.config.deployment.clanker_node_modules_path else None,
            node_script_path=__import__("pathlib").Path(self.config.deployment.node_script_path) if self.config.deployment.node_script_path else None,
        )
        rewards_claimer = ClankerRewardsClaimer(
            rpc_url=self.config.deployment.base_rpc_url,
            private_key=self.config.wallets.deployer_signer_private_key,
        )

        # Initialize Telegram worker
        telegram = TelegramWorker(
            self.db,
            review_expiry_seconds=self.config.app.review_expiry_seconds,
            bot_token=self.config.telegram.bot_token or None,
            chat_id=self.config.telegram.chat_id or None,
            message_thread_id=self.config.telegram.message_thread_id,
            thread_review_id=self.config.telegram.thread_review_id,
            thread_deploy_id=self.config.telegram.thread_deploy_id,
            thread_claim_id=self.config.telegram.thread_claim_id,
            thread_ops_id=self.config.telegram.thread_ops_id,
            thread_alert_id=self.config.telegram.thread_alert_id,
            pinata_client=pinata,
        )
        self._workers["telegram"] = telegram
        telegram.set_rewards_claimer(rewards_claimer)

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
                clanker_fee_bps=self.config.deployment.clanker_fee_bps,
                paired_fee_bps=self.config.deployment.paired_fee_bps,
                token_admin_enabled=self.config.deployment.token_admin_enabled,
                token_reward_enabled=self.config.deployment.token_reward_enabled,
                prepare_timeout_seconds=self.config.app.deploy_prepare_timeout_seconds,
                deploy_timeout_seconds=self.config.app.deploy_execute_timeout_seconds,
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
                target_handles=self.config.x_detector.target_handles,
                query_terms=self.config.x_detector.query_terms,
                max_process_concurrency=self.config.x_detector.max_process_concurrency,
                max_query_concurrency=self.config.x_detector.max_query_concurrency,
                loop_timeout_seconds=self.config.app.worker_loop_timeout_seconds,
                candidate_process_timeout_seconds=self.config.app.candidate_process_timeout_seconds,
                max_pending_notifications=self.config.app.max_pending_notifications,
            )
            x_detector.set_telegram_worker(telegram)
            self._workers["x_detector"] = x_detector
        else:
            logger.info("X detector disabled by config")

        if self.config.farcaster_detector.enabled:
            farcaster_detector = FarcasterDetectorWorker(
                self.db,
                poll_interval=self.config.farcaster_detector.poll_interval,
                api_url=self.config.farcaster_detector.api_url,
                api_key=self.config.farcaster_detector.api_key,
                max_results=self.config.farcaster_detector.max_results,
                target_handles=self.config.farcaster_detector.target_handles,
                query_terms=self.config.farcaster_detector.query_terms,
                request_timeout_seconds=self.config.farcaster_detector.request_timeout_seconds,
                max_requests_per_minute=self.config.farcaster_detector.max_requests_per_minute,
                max_process_concurrency=self.config.farcaster_detector.max_process_concurrency,
                max_query_concurrency=self.config.farcaster_detector.max_query_concurrency,
                loop_timeout_seconds=self.config.app.worker_loop_timeout_seconds,
                candidate_process_timeout_seconds=self.config.app.candidate_process_timeout_seconds,
                max_pending_notifications=self.config.app.max_pending_notifications,
                stealth_config=self.config.stealth,
            )
            farcaster_detector.set_telegram_worker(telegram)
            self._workers["farcaster_detector"] = farcaster_detector
        else:
            logger.info("Farcaster detector disabled by config")

        if self.config.gecko_detector.enabled:
            gecko_detector = GeckoDetectorWorker(
                self.db,
                poll_interval=self.config.gecko_detector.poll_interval,
                api_base_url=self.config.gecko_detector.api_base_url,
                networks=self.config.gecko_detector.networks,
                max_results=self.config.gecko_detector.max_results,
                max_pool_age_minutes=self.config.gecko_detector.max_pool_age_minutes,
                min_volume_m5_usd=self.config.gecko_detector.min_volume_m5_usd,
                min_volume_m15_usd=self.config.gecko_detector.min_volume_m15_usd,
                min_tx_count_m5=self.config.gecko_detector.min_tx_count_m5,
                min_liquidity_usd=self.config.gecko_detector.min_liquidity_usd,
                max_requests_per_minute=self.config.gecko_detector.max_requests_per_minute,
                request_timeout_seconds=self.config.gecko_detector.request_timeout_seconds,
                base_target_sources=self.config.gecko_detector.base_target_sources,
                max_process_concurrency=self.config.gecko_detector.max_process_concurrency,
                loop_timeout_seconds=self.config.app.worker_loop_timeout_seconds,
                candidate_process_timeout_seconds=self.config.app.candidate_process_timeout_seconds,
                max_pending_notifications=self.config.app.max_pending_notifications,
                stealth_config=self.config.stealth,
            )
            gecko_detector.set_telegram_worker(telegram)
            self._workers["gecko_detector"] = gecko_detector
        else:
            logger.info("Gecko detector disabled by config")

        # Start all workers
        for name, worker in self._workers.items():
            try:
                await worker.start()
                logger.info(f"Started worker: {name}")
            except Exception as exc:
                logger.error(f"Failed to start worker {name}: {exc}", exc_info=True)

        if self.config.app.cleanup_enabled:
            self._cleanup_task = asyncio.create_task(self._run_cleanup_loop())
            logger.info(
                "Enabled DB cleanup loop (interval=%ss, retention: cand=%sd review=%sd deploy=%sd reward=%sd)",
                self.config.app.cleanup_interval_seconds,
                self.config.app.retention_candidates_days,
                self.config.app.retention_reviews_days,
                self.config.app.retention_deployments_days,
                self.config.app.retention_rewards_days,
            )

        # Setup signal handlers
        self._setup_signal_handlers()

        logger.info("Supervisor started with workers: %s", list(self._workers.keys()))

    async def stop(self) -> None:
        """Stop all workers."""
        if not self._running:
            return

        logger.info("Stopping supervisor")
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

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

    async def _run_cleanup_loop(self) -> None:
        interval = max(60.0, float(self.config.app.cleanup_interval_seconds))
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                cleanup_fn = functools.partial(
                    self.db.cleanup_old_records,
                    retention_candidates_days=self.config.app.retention_candidates_days,
                    retention_reviews_days=self.config.app.retention_reviews_days,
                    retention_deployments_days=self.config.app.retention_deployments_days,
                    retention_rewards_days=self.config.app.retention_rewards_days,
                )
                summary = await loop.run_in_executor(None, cleanup_fn)
                if any(summary.values()):
                    logger.info("db.cleanup %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Cleanup loop failed: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
