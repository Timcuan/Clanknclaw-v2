from pathlib import Path

import pytest

from clankandclaw.rewards.claimer import ClankerRewardsClaimer


class _Proc:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_claim_rejects_invalid_token_address():
    claimer = ClankerRewardsClaimer(
        rpc_url="https://base-mainnet.g.alchemy.com/v2/test",
        private_key="0x" + "1" * 64,
        cli_path="/tmp/missing-cli",
    )
    result = await claimer.claim("not-an-address")
    assert result.status == "claim_failed"
    assert result.error_code == "invalid_token_address"


@pytest.mark.asyncio
async def test_claim_parses_success_tx_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cli_path = tmp_path / "clanker-sdk"
    cli_path.write_text("#!/bin/sh\n")

    async def fake_exec(*args, **kwargs):
        return _Proc(
            0,
            b'{"status":"ok","txHash":"0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n',
            b"",
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

    claimer = ClankerRewardsClaimer(
        rpc_url="https://base-mainnet.g.alchemy.com/v2/test",
        private_key="0x" + "1" * 64,
        cli_path=str(cli_path),
    )
    result = await claimer.claim("0x" + "b" * 40)
    assert result.status == "claim_success"
    assert result.tx_hash == "0x" + "a" * 64
