"""Clanker rewards claim integration via clanker-sdk CLI."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass
class ClaimFeesResult:
    status: str
    tx_hash: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ClankerRewardsClaimer:
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        cli_path: str | None = None,
    ) -> None:
        self.rpc_url = rpc_url
        self.private_key = private_key
        default_cli = Path(__file__).parent.parent.parent / "node_modules" / ".bin" / "clanker-sdk"
        self.cli_path = cli_path or os.getenv("CLANKER_CLI_PATH", str(default_cli))

    async def claim(self, token_address: str) -> ClaimFeesResult:
        if not _EVM_ADDRESS_RE.fullmatch(token_address):
            return ClaimFeesResult(
                status="claim_failed",
                error_code="invalid_token_address",
                error_message="token_address must be a valid EVM address",
            )
        cli = Path(self.cli_path)
        if not cli.exists():
            return ClaimFeesResult(
                status="claim_failed",
                error_code="cli_not_found",
                error_message=f"clanker-sdk CLI not found at {cli}",
            )

        proc = await asyncio.create_subprocess_exec(
            str(cli),
            "rewards",
            "claim",
            "--token",
            token_address,
            "--chain",
            "base",
            "--rpc",
            self.rpc_url,
            "--private-key",
            self.private_key,
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return ClaimFeesResult(
                status="claim_failed",
                error_code="claim_command_failed",
                error_message=stderr or stdout or f"exit code {proc.returncode}",
            )

        tx_hash = self._extract_tx_hash(stdout)
        return ClaimFeesResult(status="claim_success", tx_hash=tx_hash)

    def _extract_tx_hash(self, stdout: str) -> str | None:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            for key in ("txHash", "transactionHash", "hash"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith("0x") and len(value) == 66:
                    return value
        return None

