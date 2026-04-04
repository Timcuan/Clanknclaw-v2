from typing import Protocol

from clankandclaw.models.token import DeployRequest, DeployResult


class BaseDeployer(Protocol):
    async def prepare(self, deploy_request: DeployRequest) -> dict: ...

    async def preflight(self, deploy_request: DeployRequest) -> None: ...

    async def deploy(self, deploy_request: DeployRequest) -> DeployResult: ...
