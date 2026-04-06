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

DeployCallable = Callable[[dict[str, Any], DeployRequest], Awaitable[DeployResult]]

_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_IPFS_URI_RE = re.compile(r"^ipfs://[a-zA-Z0-9]+")
_TOKEN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,49}$")
_TOKEN_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,10}$")
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")

# Default script path (relative to project root)
_DEFAULT_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "clanker_deploy.mjs"

# Default Node modules path (project-local)
_DEFAULT_NODE_MODULES_PATH = Path(__file__).parent.parent.parent / "node_modules"
_HARDCODED_PAIRED_TOKEN = "0x4200000000000000000000000000000000000006"  # WETH on Base
_HARDCODED_STARTING_MARKET_CAP_ETH = 10.0
_ADMIN_INTERFACE_SPLIT_BPS = 10   # 0.1%
_REWARD_RECIPIENT_SPLIT_BPS = 9990  # 99.9%


def build_clanker_v4_config(deploy_request: DeployRequest) -> dict:
    """
    Build Clanker v4.0.0 SDK configuration.
    
    Based on Clanker v4.0.0 SDK documentation:
    https://clanker.gitbook.io/clanker-documentation/sdk/v4.0.0
    https://github.com/clanker-devco/clanker-sdk
    
    The Clanker SDK v4 is a TypeScript SDK that uses viem for blockchain interactions.
    This function builds the configuration object that can be passed to the SDK.
    """
    description = deploy_request.metadata_description or (
        f"{deploy_request.token_name} ({deploy_request.token_symbol}) on Base."
    )

    context: dict[str, Any] = {
        "interface": "Clank&Claw",
        "platform": deploy_request.source or "automated",
        "messageId": deploy_request.source_event_id or deploy_request.candidate_id,
        "id": deploy_request.candidate_id,
    }

    # Build metadata: description is key for Clanker v4 deployment standards
    description = deploy_request.metadata_description or deploy_request.raw_context_excerpt or ""
    metadata: dict[str, Any] = {"description": description}

    config = {
        "name": deploy_request.token_name,
        "symbol": deploy_request.token_symbol,
        "image": deploy_request.image_uri,
        "metadata": metadata,
        "vanity": True,  # Generate 0xb07 suffix
        **({"tokenAdmin": deploy_request.token_admin} if deploy_request.token_admin_enabled else {}),
        "context": context,
        # Pool: pairedToken is passed through; tick/positions computed by Node.js script
        "pool": {
            "pairedToken": _HARDCODED_PAIRED_TOKEN,
        },
        "startingMarketCapEth": _HARDCODED_STARTING_MARKET_CAP_ETH,
        # Fee configuration - static 1% fees
        "fees": {
            "type": "static",
            "clankerFee": deploy_request.clanker_fee_bps if deploy_request.clanker_fee_bps is not None else deploy_request.tax_bps,
            "pairedFee": deploy_request.paired_fee_bps if deploy_request.paired_fee_bps is not None else deploy_request.tax_bps,
        },
        # Rewards configuration (omitted when disabled)
        **({"rewards": {
            "recipients": [
                {
                    "recipient": deploy_request.token_admin,
                    "admin": deploy_request.token_admin,
                    "bps": _ADMIN_INTERFACE_SPLIT_BPS,  # 0.1% spoof/admin interface
                    "token": "Both",  # Receive fees in both tokens
                },
                {
                    "recipient": deploy_request.fee_recipient,
                    "admin": deploy_request.token_admin,
                    "bps": _REWARD_RECIPIENT_SPLIT_BPS,  # 99.9% to reward recipient
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
        tx_hash = data.get("txHash")
        contract_address = data.get("contractAddress")
        if not isinstance(tx_hash, str) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
            return DeployResult(
                deploy_request_id=deploy_request_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="invalid_sdk_output",
                error_message="SDK success response missing valid txHash",
                completed_at=completed_at,
            )
        if not isinstance(contract_address, str) or not _EVM_ADDRESS_RE.fullmatch(contract_address):
            return DeployResult(
                deploy_request_id=deploy_request_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="invalid_sdk_output",
                error_message="SDK success response missing valid contractAddress",
                completed_at=completed_at,
            )
        return DeployResult(
            deploy_request_id=deploy_request_id,
            status="deploy_success",
            tx_hash=tx_hash,
            contract_address=contract_address,
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

    Calls scripts/clanker_deploy.mjs via subprocess, using this project's
    local node_modules for clanker-sdk and viem dependencies.
    """

    def __init__(
        self,
        execute: DeployCallable | None = None,
        rpc_url: str | None = None,
        node_script_path: Path | None = None,
        node_modules_path: Path | None = None,
    ) -> None:
        self._execute = execute
        self.rpc_url = rpc_url or os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
        self._node_script_path = node_script_path or Path(
            os.getenv("NODE_SCRIPT_PATH", str(_DEFAULT_SCRIPT_PATH))
        )
        self._node_modules_path = node_modules_path or Path(
            os.getenv("CLANKER_NODE_MODULES_PATH", str(_DEFAULT_NODE_MODULES_PATH))
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
        if not _TOKEN_NAME_RE.fullmatch(name):
            raise ValueError("token_name contains unsupported characters")

        if not symbol or len(symbol) > 10:
            raise ValueError("token_symbol must be 1–10 characters")

        if not _TOKEN_SYMBOL_RE.fullmatch(symbol):
            raise ValueError("token_symbol must contain only A-Z0-9 and be 2-10 chars")

        if deploy_request.token_admin_enabled:
            if not _EVM_ADDRESS_RE.fullmatch(deploy_request.token_admin):
                raise ValueError("token_admin must be a valid EVM address")
            if deploy_request.token_admin.lower() == _ZERO_ADDRESS:
                raise ValueError("token_admin must not be zero address")
        if deploy_request.token_reward_enabled and not deploy_request.token_admin_enabled:
            raise ValueError("token_reward_enabled requires token_admin_enabled=true")
        if not _EVM_ADDRESS_RE.fullmatch(deploy_request.tax_recipient):
            raise ValueError("tax_recipient must be a valid EVM address")
        if deploy_request.tax_recipient.lower() == _ZERO_ADDRESS:
            raise ValueError("tax_recipient must not be zero address")
        if not _EVM_ADDRESS_RE.fullmatch(deploy_request.fee_recipient):
            raise ValueError("fee_recipient must be a valid EVM address")
        if deploy_request.fee_recipient.lower() == _ZERO_ADDRESS:
            raise ValueError("fee_recipient must not be zero address")

        clanker_fee = deploy_request.clanker_fee_bps if deploy_request.clanker_fee_bps is not None else deploy_request.tax_bps
        paired_fee = deploy_request.paired_fee_bps if deploy_request.paired_fee_bps is not None else deploy_request.tax_bps
        if not 0 <= clanker_fee <= 10000:
            raise ValueError("clanker_fee_bps must be between 0 and 10000")
        if not 0 <= paired_fee <= 10000:
            raise ValueError("paired_fee_bps must be between 0 and 10000")
        if deploy_request.token_reward_enabled:
            if _ADMIN_INTERFACE_SPLIT_BPS + _REWARD_RECIPIENT_SPLIT_BPS != 10000:
                raise ValueError("internal reward split misconfigured: bps must sum to 10000")

        if not _IPFS_URI_RE.match(deploy_request.image_uri):
            raise ValueError("image_uri must be a valid IPFS URI (ipfs://...)")
        if deploy_request.metadata_description is not None:
            desc = deploy_request.metadata_description.strip()
            if len(desc) < 8 or len(desc) > 280:
                raise ValueError("metadata_description must be 8-280 characters")
        if deploy_request.context_url and not deploy_request.context_url.startswith(("https://", "http://")):
            raise ValueError("context_url must be an http(s) URL")

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
        with NODE_PATH pointing to local node_modules, parses the JSON output.
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

        node_modules = self._node_modules_path.resolve()
        if not node_modules.exists():
            return DeployResult(
                deploy_request_id=deploy_request.candidate_id,
                status="deploy_failed",
                latency_ms=0,
                error_code="sdk_not_installed",
                error_message=f"Node modules not found at {node_modules}. Run `npm install`.",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        tmp_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(config, f)
                tmp_file = f.name

            env = os.environ.copy()
            env["DEBUG"] = "true"
            env["NODE_PATH"] = str(node_modules)
            env["BASE_RPC_URL"] = self.rpc_url
            signer_value = (deploy_request.signer_wallet or "").strip()
            if _PRIVATE_KEY_RE.fullmatch(signer_value):
                env["DEPLOYER_SIGNER_PRIVATE_KEY"] = signer_value

            logger.info(f"Deploying {deploy_request.token_symbol!r} via Clanker SDK v4")
            if os.path.exists(tmp_file):
                with open(tmp_file, "r") as f:
                    logger.info(f"Deployment Payload: {f.read()}")

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
                for line in stderr.splitlines():
                    if "[CNC-DEBUG]" in line:
                        logger.info(f"SDK DEBUG: {line}")
                    else:
                        logger.debug(f"SDK stderr: {line[:500]}")

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
