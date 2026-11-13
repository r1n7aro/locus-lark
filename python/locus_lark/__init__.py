"""``locus.lark``: Feishu / Lark from Locus scripts.

Services act as the signed-in user; ``locus.lark.bot`` gives the same
services acting as the app's bot. Every list is an async iterator, every
object keeps the identity it came from::

    lark = locus.lark
    chat = await lark.chats.find("项目群")
    async for message in chat.messages(since="2026-11-01"):
        print(message.sender.name, message.text)
    doc = await lark.open("https://example.feishu.cn/wiki/…")
    text = await doc.markdown()

Services: ``chats``, ``messages``, ``users``, ``wiki``, ``docs``, ``drive``,
``sheets``, ``base``; ``auth`` (sign-in), ``api`` (raw requests). Nothing is
imported until it is used; classes and helpers (``text_block``,
``where_filter``, ...) are attributes of this module.
"""

from __future__ import annotations

import importlib
from typing import Any

from ._core import Lark, LarkAuthError, LarkError, LarkScopeError, _client

__all__ = ["Lark", "LarkError", "LarkAuthError", "LarkScopeError"]

# Classes, loaded on first use: name -> module.
_CLASSES = {
    "Chat": "_im", "Message": "_im", "Reaction": "_im", "Pin": "_im", "Announcement": "_im",
    "User": "_contact",
    "Document": "_docs", "Block": "_docs", "HistoryVersion": "_docs", "SearchResult": "_docs",
    "WikiSpace": "_wiki", "WikiNode": "_wiki",
    "DriveFile": "_drive", "Folder": "_drive",
    "Spreadsheet": "_sheets", "Worksheet": "_sheets",
    "Base": "_bitable", "Table": "_bitable", "Record": "_bitable", "Field": "_bitable", "View": "_bitable",
    "Comment": "_cloud", "CommentReply": "_cloud", "Member": "_cloud",
    "AuthStatus": "_auth", "LoginRequest": "_auth",
    # Helpers.
    "text_block": "_docs", "BLOCK_TYPES": "_docs", "where_filter": "_bitable", "FIELD_TYPES": "_bitable",
    "column_letter": "_sheets",
}
# Attributes of the default (user) client served by the module.
_CLIENT = {"chats", "messages", "users", "docs", "wiki", "drive", "sheets", "base", "auth", "api",
           "user", "bot", "open", "request", "pages"}


def __getattr__(name: str) -> Any:
    if name in _CLIENT:
        return getattr(_client("user"), name)
    module = _CLASSES.get(name)
    if module is not None:
        value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'locus.lark' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _CLIENT | set(_CLASSES) | {"me", "status", "login", "logout"})


async def me() -> Any:
    """The signed-in user."""
    return await _client("user").users.me()


async def status() -> Any:
    """The sign-in state (``AuthStatus``)."""
    return await _client("user").auth.status()


async def login(*, scopes: list[str] | None = None, domains: list[str] | None = None,
                optional: list[str] | None = None, open_browser: bool = True) -> Any:
    """Starts a sign-in (``LoginRequest``: show ``url`` to the user, then
    ``await request.wait()``); see ``lark.auth.login`` for the arguments."""
    return await _client("user").auth.login(scopes=scopes, domains=domains, optional=optional,
                                            open_browser=open_browser)


async def logout() -> Any:
    return await _client("user").auth.logout()
