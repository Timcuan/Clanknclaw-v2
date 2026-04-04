from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from clankandclaw.models.token import DeployRequest, DeployResult


DeployExecutor = Callable[[dict[str, Any], DeployRequest], Awaitable[DeployResult]]


def build_clanker_payload(deploy_request: DeployRequest) -> dict:
    payload = {
        "name": deploy_request.token_name,
        "symbol": deploy_request.token_symbol,
        "image": deploy_request.image_uri,
        "fees": {
            "type": "static",
            "clankerFee": deploy_request.tax_bps,
            "pairedFee": deploy_request.tax_bps,
        },
        "metadataUri": deploy_request.metadata_uri,
    }

    if deploy_request.token_admin_enabled:
        payload["tokenAdmin"] = deploy_request.token_admin

    if deploy_request.token_reward_enabled:
        payload["rewards"] = {
            "recipients": [
                {
                    "admin": deploy_request.token_admin,
                    "recipient": deploy_request.fee_recipient,
                    "bps": 10000,
                    "token": "Both",
                }
            ]
        }

    return payload


class ClankerDeployer:
    def __init__(self, execute: DeployExecutor | None = None) -> None:
        self._execute = execute

    async def prepare(self, deploy_request: DeployRequest) -> dict:
        return build_clanker_payload(deploy_request)

    async def preflight(self, deploy_request: DeployRequest) -> None:
        build_clanker_payload(deploy_request)

    async def deploy(self, deploy_request: DeployRequest) -> DeployResult:
        payload = await self.prepare(deploy_request)
        await self.preflight(deploy_request)

        if self._execute is not None:
            return await self._execute(payload, deploy_request)

        return DeployResult(
            deploy_request_id=deploy_request.candidate_id,
            status="deploy_failed",
            latency_ms=0,
            error_code="not_implemented",
            error_message="Clanker deployment executor is not configured",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
