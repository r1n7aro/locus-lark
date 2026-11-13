"""``lark.api``: raw requests to any Open Platform endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from ._core import Service


class Api(Service):
    """Requests with the client's identity and token, for endpoints the
    object model does not cover. Each returns the reply's ``data``.

    ``await lark.api.get("/open-apis/calendar/v4/calendars")``;
    ``async for item in lark.api.pages("GET", "/open-apis/...", page_size=50)``.
    """

    async def get(self, path: str, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.lark.request("GET", path, params=params, **kwargs)

    async def post(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.lark.request("POST", path, params=params, json=json, **kwargs)

    async def put(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.lark.request("PUT", path, params=params, json=json, **kwargs)

    async def patch(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.lark.request("PATCH", path, params=params, json=json, **kwargs)

    async def delete(self, path: str, json: Any = None, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return await self.lark.request("DELETE", path, params=params, json=json, **kwargs)

    def pages(self, method: str, path: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        """Items of a paged list; see ``Lark.pages``."""
        return self.lark.pages(method, path, **kwargs)
