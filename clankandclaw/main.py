"""Clank&Claw MVP entrypoint."""

import asyncio
import logging
import sys
from pathlib import Path

from clankandclaw.config import load_config
from clankandclaw.core.supervisor import Supervisor
from clankandclaw.database.manager import DatabaseManager


def setup_logging(log_level: str) -> None:
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )


async def async_main() -> None:
    """Async main function."""
    # Load configuration
    config = load_config(Path("config.yaml"))
    setup_logging(config.app.log_level)
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Clank&Claw MVP")
    logger.info(f"Log level: {config.app.log_level}")
    logger.info(f"Review expiry: {config.app.review_expiry_seconds}s")
    logger.info(f"Platform: {config.deployment.platform}")
    logger.info(f"Tax BPS: {config.deployment.tax_bps}")
    
    # Initialize database
    db = DatabaseManager(Path("clankandclaw.db"))
    db.initialize()
    logger.info("Database initialized")
    
    # Create and run supervisor
    supervisor = Supervisor(config, db)
    
    try:
        await supervisor.run()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        raise
    finally:
        logger.info("Clank&Claw MVP stopped")


def main() -> None:
    """Main entrypoint."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
