from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile

from clankandclaw.models.token import DeployRequest, DeployResult

logger = logging.getLogger(__name__)

DeployExecutor = Callable[[dict[str, Any], DeployRequest], Awaitable[DeployResult]]

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_IPFS_URI_RE = re.compile(r"^ipfs://[a-zA-Z0-9]+")

# Default script path (relative to project root)
_DEFAULT_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "clanker_deploy.mjs"

# Default Executor path (sibling directory)
_DEFAULT_EXECUTOR_PATH = Path(__file__).parent.parent.parent.parent / "Clank n Claw - Executor"


def build_clanker_v4_config(deploy_request: DeployRequest) -> dict:
    """
    Build Clanker v4.0.0 SDK configuration.
    
    Based on Clanker v4.0.0 SDK documentation:
    https://clanker.gitbook.io/clanker-documentation/sdk/v4.0.0
    https://github.com/clanker-devco/clanker-sdk
    
    The Clanker SDK v4 is a TypeScript SDK that uses viem for blockchain interactions.
    This function builds the configuration object that can be passed to the SDK.
    """
    config = {
        "name": deploy_request.token_name,
        "symbol": deploy_request.token_symbol,
        "image": deploy_request.image_uri,
        "metadata": {
            "description": f"Token deployed via Clank&Claw from candidate {deploy_request.candidate_id}",
        },
        **({"tokenAdmin": deploy_request.token_admin} if deploy_request.token_admin_enabled else {}),
        "context": {
            "interface": "Clank&Claw",
            "platform": "automated",
            "messageId": deploy_request.candidate_id,
            "id": deploy_request.candidate_id,
        },
        # Pool: pairedToken is passed through; tick/positions computed by Node.js script
        "pool": {
            "pairedToken": "0x4200000000000000000000000000000000000006",  # WETH on Base
        },
        # Fee configuration - static 1% fees
        "fees": {
            "type": "static",
            "clankerFee": deploy_request.tax_bps,  # In bps (1000 = 10%)
            "pairedFee": deploy_request.tax_bps,
        },
        # Rewards configuration (omitted when disabled)
        **({"rewards": {
            "recipients": [
                {
                    "recipient": deploy_request.fee_recipient,
                    "admin": deploy_request.token_admin,
                    "bps": 10000,  # 100% to creator
                    "token": "Both",  # Receive fees in both tokens
                }
            ]
        }} if deploy_request.token_reward_enabled else {}),
    }
    
    return config


def parse_sdk_output(
    stdout: str,
    stderr: str,
    exit_code: int,
    deploy_request_id: str,
) -> DeployResult:
    """
    Parse output from the Node.js wrapper script into a DeployResult.
    Never raises exceptions — always returns a valid DeployResult.
    """
    completed_at = datetime.now(timezone.utc).isoformat()

    if exit_code != 0:
        error_code = "subprocess_failed"
        error_message = stderr.strip() or f"Process exited with code {exit_code}"
        try:
            data = json.loads(stderr.strip())
            error_code = data.get("errorCode", error_code)
            error_message = data.get("errorMessage", error_message)
        except Exception:
            # Detect Node.js module-not-found errors for clanker-sdk / viem
            if ("ERR_MODULE_NOT_FOUND" in stderr or "Cannot find package" in stderr) and (
                "clanker-sdk" in stderr or "viem" in stderr
            ):
                error_code = "sdk_not_installed"
        return DeployResult(
            deploy_request_id=deploy_request_id,
            status="deploy_failed",
            latency_ms=0,
            error_code=error_code,
            error_message=error_message,
            completed_at=completed_at,
        )

    try:
        data = json.loads(stdout.strip())
    except Exception:
        return DeployResult(
            deploy_request_id=deploy_request_id,
            status="deploy_failed",
            latency_ms=0,
            error_code="parse_error",
            error_message=f"Failed to parse SDK output: {stdout[:200]}",
            completed_at=completed_at,
        )

    if data.get("status") == "success":
        return DeployResult(
            deploy_request_id=deploy_request_id,
            status="deploy_success",
            tx_hash=data.get("txHash"),
            contract_address=data.get("contractAddress"),
            latency_ms=0,
            completed_at=completed_at,
        )

    return DeployResult(
        deploy_request_id=deploy_request_id,
        status="deploy_failed",
        latency_ms=0,
        error_code=data.get("errorCode", "sdk_error"),
        error_message=data.get("errorMessage", "Unknown SDK error"),
        completed_at=completed_at,
    )


class ClankerDeployer:
    """
    Clanker v4.0.0 SDK deployer.

    Calls scripts/clanker_deploy.mjs via subprocess, using the Executor project's
    node_modules for clanker-sdk and viem dependencies.
    """

    def __init__(
        self,
        execute: DeployExecutor | None = None,
        rpc_url: str | None = None,
        node_script_path: Path | None = None,
        executor_path: Path | None = None,
    ) -> None:
        self._execute = execute
        self.rpc_url = rpc_url or os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
        self._node_script_path = node_script_path or Path(
            os.getenv("NODE_SCRIPT_PATH", str(_DEFAULT_SCRIPT_PATH))
        )
        self._executor_path = executor_path or Path(
            os.getenv("EXECUTOR_PATH", str(_DEFAULT_EXECUTOR_PATH))
        )
        self._sdk_available = self._check_sdk_availability()

    def _check_sdk_availability(self) -> bool:
        """Check if Node.js is available for SDK execution."""
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                timeout=5,
            )
            available = result.returncode == 0
            if not available:
                logger.warning("Node.js not found, Clanker SDK deployment will not be available")
            return available
        except Exception:
            logger.warning("Node.js not found, Clanker SDK deployment will not be available")
            return False

    async def prepare(self, deploy_request: DeployRequest) -> dict:
        return build_clanker_v4_config(deploy_request)

    async def preflight(self, deploy_request: DeployRequest) -> None:
        """Validate deployment configuration before executing."""
        name = deploy_request.token_name
        symbol = deploy_request.token_symbol

        if not name or len(name) > 50:
            raise ValueError("token_name must be 1–50 characters")

        if not symbol or len(symbol) > 10:
            raise ValueError("token_symbol must be 1–10 characters")

        if symbol != symbol.upper():
            raise ValueError("token_symbol must be uppercase")

        if deploy_request.token_admin_enabled:
            if not _EVM_ADDRESS_RE.fullmatch(deploy_request.token_admin):
                raise ValueError("token_admin must be a valid EVM address")

        if not _IPFS_URI_RE.match(deploy_request.image_uri):
            raise ValueError("image_uri must be a valid IPFS URI (ipfs://...)")

        if not 0 <= deploy_request.tax_bps <= 10000:
            raise ValueError("tax_bps must be between 0 and 10000")

        if not self.rpc_url.startswith("https://"):
            raise ValueError("BASE_RPC_URL must use HTTPS protocol")

    async def deploy(self, deploy_request: DeployRequest) -> DeployResult:
        start_time = datetime.now(timezone.utc)

        try:
            config = await self.prepare(deploy_request)
            await self.preflight(deploy_request)
        except ValueError as exc:
            return DeployResult(
                deploy_request_id=deploy_request.candidate_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="invalid_config",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        if self._execute is not None:
            return await self._execute(config, deploy_request)

        if not self._sdk_available:
            return DeployResult(
                deploy_request_id=deploy_request.candidate_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="sdk_not_available",
                error_message="Node.js required for Clanker SDK deployment. Install from https://nodejs.org",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        try:
            result = await self._execute_with_sdk(deploy_request, config)
        except Exception as exc:
            logger.error(f"SDK execution failed: {exc}", exc_info=True)
            result = DeployResult(
                deploy_request_id=deploy_request.candidate_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="sdk_execution_failed",
                error_message=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        end_time = datetime.now(timezone.utc)
        result.latency_ms = int((end_time - start_time).total_seconds() * 1000)
        return result

    async def _execute_with_sdk(
        self, deploy_request: DeployRequest, config: dict[str, Any]
    ) -> DeployResult:
        """
        Execute deployment via the Node.js wrapper script.

        Writes config to a temp JSON file, spawns the Node.js script as a subprocess
        with NODE_PATH pointing to the Executor's node_modules, parses the JSON output.
        The temp file is always cleaned up in the finally block.
        """
        script_path = self._node_script_path.resolve()
        if not script_path.exists():
            return DeployResult(
                deploy_request_id=deploy_request.candidate_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="script_not_found",
                error_message=f"Node.js wrapper script not found: {script_path}",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        node_modules = (self._executor_path / "node_modules").resolve()
        if not node_modules.exists():
            logger.warning(f"Executor node_modules not found at {node_modules}")

        tmp_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(config, f)
                tmp_file = f.name

            env = os.environ.copy()
            env["NODE_PATH"] = str(node_modules)
            env["BASE_RPC_URL"] = self.rpc_url

            logger.info(f"Deploying {deploy_request.token_symbol!r} via Clanker SDK v4")

            proc = await asyncio.create_subprocess_exec(
                "node", str(script_path), tmp_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=120
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return DeployResult(
                    deploy_request_id=deploy_request.candidate_id,
                    status="deploy_failed",
                    latency_ms=0,
                    error_code="timeout",
                    error_message="Deployment timed out after 120 seconds",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            if stderr:
                logger.debug(f"SDK stderr: {stderr[:500]}")

            return parse_sdk_output(stdout, stderr, exit_code, deploy_request.candidate_id)

        finally:
            if tmp_file:
                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass


# Legacy function for backward compatibility
def build_clanker_payload(deploy_request: DeployRequest) -> dict:
    """Legacy function - use build_clanker_v4_config instead."""
    return build_clanker_v4_config(deploy_request)
