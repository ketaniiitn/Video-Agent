from typing import Protocol

from app.gateway.client import Usage


class GatewayClient(Protocol):
    async def complete_json(
        self,
        alias: str,
        messages: list[dict],
        schema_name: str,
    ) -> tuple[dict, Usage]: ...
