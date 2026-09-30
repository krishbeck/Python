"""Thin async client for the ServiceNow REST Table API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError


class ServiceNowError(ToolError):
    """Raised when ServiceNow returns an error or the client is misconfigured.

    Subclasses ToolError so the MCP server passes the message back to the model.
    """


@dataclass(frozen=True)
class ServiceNowConfig:
    instance_url: str
    username: str | None = None
    password: str | None = None
    token: str | None = None
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> "ServiceNowConfig":
        instance_url = os.environ.get("SERVICENOW_INSTANCE_URL", "").strip().rstrip("/")
        if not instance_url:
            raise ServiceNowError("SERVICENOW_INSTANCE_URL is not set")
        if not instance_url.startswith(("https://", "http://")):
            instance_url = f"https://{instance_url}"

        config = cls(
            instance_url=instance_url,
            username=os.environ.get("SERVICENOW_USERNAME") or None,
            password=os.environ.get("SERVICENOW_PASSWORD") or None,
            token=os.environ.get("SERVICENOW_TOKEN") or None,
            timeout=float(os.environ.get("SERVICENOW_TIMEOUT", "30")),
        )
        if not config.token and not (config.username and config.password):
            raise ServiceNowError(
                "Set SERVICENOW_TOKEN, or both SERVICENOW_USERNAME and SERVICENOW_PASSWORD"
            )
        return config


class ServiceNowClient:
    """Wraps /api/now/table/{table} with the handful of calls the MCP tools need."""

    def __init__(self, config: ServiceNowConfig, transport: httpx.AsyncBaseTransport | None = None):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth = None
        if config.token:
            headers["Authorization"] = f"Bearer {config.token}"
        else:
            auth = httpx.BasicAuth(config.username or "", config.password or "")

        self._http = httpx.AsyncClient(
            base_url=f"{config.instance_url}/api/now",
            headers=headers,
            auth=auth,
            timeout=config.timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServiceNowError(f"Could not reach ServiceNow: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                error = response.json().get("error", {})
                detail = error.get("message", detail)
                if error.get("detail"):
                    detail = f"{detail}: {error['detail']}"
            except ValueError:
                pass
            raise ServiceNowError(f"ServiceNow returned HTTP {response.status_code}: {detail}")

        if response.status_code == 204 or not response.content:
            return None
        return response.json().get("result")

    async def query(
        self,
        table: str,
        query: str = "",
        fields: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        display_value: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "sysparm_limit": limit,
            "sysparm_offset": offset,
            "sysparm_display_value": str(display_value).lower(),
            "sysparm_exclude_reference_link": "true",
        }
        if query:
            params["sysparm_query"] = query
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        return await self._request("GET", f"/table/{table}", params=params) or []

    async def get(
        self, table: str, sys_id: str, fields: list[str] | None = None, display_value: bool = True
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sysparm_display_value": str(display_value).lower(),
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        return await self._request("GET", f"/table/{table}/{sys_id}", params=params)

    async def get_by_number(self, table: str, number: str) -> dict[str, Any]:
        if not number or "^" in number:
            raise ServiceNowError(f"Invalid record number: {number!r}")
        records = await self.query(table, query=f"number={number}", limit=1)
        if not records:
            raise ServiceNowError(f"No {table} record found with number {number}")
        return records[0]

    async def create(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", f"/table/{table}", json=data, params={"sysparm_display_value": "true"}
        )

    async def update(self, table: str, sys_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/table/{table}/{sys_id}", json=data, params={"sysparm_display_value": "true"}
        )
