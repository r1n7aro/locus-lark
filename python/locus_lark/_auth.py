"""``lark.auth``: the sign-in, managed by the plugin's ``lark_auth`` tool.

The user usually signs in on the plugin's Settings tab (Plugins → 飞书 / Lark).
Scripts can check the state, ask for more permissions or start a sign-in
whose link the user opens.
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Any

from ._core import LarkAuthError, Service, call_auth, forget_credentials
from ._util import from_timestamp

# The device code expires after a few minutes; one wait call covers it.
_WAIT_TIMEOUT = 600.0
# Longest single wait of the tool (its process timeout is 15 minutes).
_WAIT_CHUNK = 600.0


@dataclass(frozen=True)
class AuthStatus:
    """Sign-in state. ``app_ready``: an app is set up (``app_id``);
    ``signed_in``: the user is signed in as ``user_name`` with ``scopes``."""

    brand: str
    app_ready: bool
    app_id: str | None
    app_source: str | None
    signed_in: bool
    user_name: str | None = None
    open_id: str | None = None
    tenant: str | None = None
    scopes: tuple[str, ...] = ()
    expires_at: _dt.datetime | None = None
    refresh_expires_at: _dt.datetime | None = None
    pending: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_reply(cls, reply: dict[str, Any]) -> "AuthStatus":
        user = reply.get("user") or {}
        return cls(
            brand=str(reply.get("brand") or "feishu"),
            app_ready=bool(reply.get("appReady")),
            app_id=reply.get("appId"),
            app_source=reply.get("appSource"),
            signed_in=bool(reply.get("signedIn")),
            user_name=user.get("name"),
            open_id=user.get("openId"),
            tenant=user.get("tenantName") or user.get("tenantKey"),
            scopes=tuple(str(reply.get("scope") or "").split()),
            expires_at=from_timestamp(reply.get("expiresAt")),
            refresh_expires_at=from_timestamp(reply.get("refreshExpiresAt")),
            pending=reply.get("pending"),
        )


@dataclass
class LoginRequest:
    """A sign-in waiting for the user: they open ``url`` (and confirm
    ``user_code`` if asked). ``await request.wait()`` returns once they did."""

    url: str
    user_code: str
    kind: str
    expires_in: int

    async def wait(self, timeout: float = _WAIT_TIMEOUT) -> AuthStatus:
        """Waits until the user confirmed (or the link expired, ``LarkAuthError``)."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LarkAuthError("Timed out waiting for the confirmation in Feishu", code="timeout")
            chunk = min(remaining, _WAIT_CHUNK)
            reply = await call_auth("wait", timeout=chunk + 30, timeoutSeconds=max(1, int(chunk)))
            if reply.get("done"):
                forget_credentials()
                return AuthStatus.from_reply(reply.get("status") or {})


class Auth(Service):
    async def status(self) -> AuthStatus:
        return AuthStatus.from_reply(await call_auth("status"))

    async def login(self, *, scopes: list[str] | None = None, domains: list[str] | None = None,
                    optional: list[str] | None = None, open_browser: bool = True) -> LoginRequest:
        """Starts a sign-in for the default permissions, or for ``domains``
        (``wiki``, ``docs``, ``drive``, ``im``, ``contact``, ``sheets``,
        ``base``), ``optional`` groups (``send_as_user``: send and reply as
        the user; ``chat_admin``: disband chats, announcements, chat pins;
        ``contact_lookup``: find users by email or phone; ``wiki_rename``) and
        extra ``scopes``. Permissions granted before are kept. Opens the link
        in the browser unless ``open_browser`` is false; show ``url`` to the
        user either way, then ``await request.wait()``."""
        reply = await call_auth("login", scopes=scopes, domains=domains, optional=optional, open=open_browser)
        return LoginRequest(url=reply["url"], user_code=str(reply.get("userCode") or ""), kind="login",
                            expires_in=int(reply.get("expiresIn") or 0))

    async def catalog(self) -> dict[str, Any]:
        """The permission domains and optional groups, with what is granted."""
        return await call_auth("catalog")

    async def register_app(self, *, open_browser: bool = True) -> LoginRequest:
        """Creates a personal app for Locus in the user's tenant (they confirm
        in Feishu); afterwards ``login()`` signs in with it."""
        reply = await call_auth("register", open=open_browser)
        return LoginRequest(url=reply["url"], user_code=str(reply.get("userCode") or ""), kind="register",
                            expires_in=int(reply.get("expiresIn") or 0))

    async def logout(self) -> AuthStatus:
        reply = await call_auth("logout")
        forget_credentials()
        return AuthStatus.from_reply(reply)
