"""Client core: credentials, the HTTP connection, errors, paging, resources.

Requests go straight from the script's process to the Feishu / Lark Open
Platform over one keep-alive connection pool per event loop. The plugin's
`lark_auth` tool, which alone holds the app secret and the refresh token,
hands out short-lived access tokens; they are cached per identity for this
process and renewed shortly before they expire or when the server rejects
them.
"""

from __future__ import annotations

import asyncio
import json
import time
import weakref
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

AUTH_TOOL = "lark_auth"
# Renew this long before an access token expires.
_RENEW_MARGIN = 120.0
_TOKEN_ERRORS = {99991661, 99991663, 99991664, 99991665, 99991668, 99991677, 20005}
_USER_SCOPE_ERROR = 99991679
_APP_SCOPE_ERROR = 99991672
_RATE_LIMIT_ERRORS = {99991400, 11232, 1254290}
_RETRY_STATUS = {429, 500, 502, 503, 504}


class LarkError(Exception):
    """A request the Open Platform refused or could not answer.

    ``code`` and ``msg`` are the platform's, ``log_id`` identifies the request
    for support, ``status`` is the HTTP status and ``data`` the raw reply.
    """

    def __init__(self, message: str, *, code: int | None = None, status: int | None = None,
                 log_id: str | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.log_id = log_id
        self.data = data


class LarkAuthError(LarkError):
    """No usable sign-in: the app is not set up, or the user is not signed in."""


class LarkScopeError(LarkError):
    """The sign-in or the app lacks permissions (``scopes``).

    ``app_side`` is true when the app itself must enable them (developer
    console, ``console_url``); otherwise ``await locus.lark.login(scopes=...)``
    asks the user to grant them.
    """

    def __init__(self, message: str, *, scopes: list[str], app_side: bool, console_url: str | None = None,
                 **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.scopes = scopes
        self.app_side = app_side
        self.console_url = console_url


# ── credentials ─────────────────────────────────────────────────────────


class Credential:
    """One access token for an identity (``user`` or ``bot``)."""

    __slots__ = ("identity", "_token", "expires_at", "open_base", "brand", "scopes", "open_id", "app_id")

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.identity = str(payload.get("identity") or "user")
        self._token = str(payload.get("accessToken") or "")
        self.expires_at = float(payload.get("expiresAt") or 0)
        self.open_base = str(payload.get("openBase") or "https://open.feishu.cn").rstrip("/")
        self.brand = str(payload.get("brand") or "feishu")
        self.scopes = frozenset(str(payload.get("scope") or "").split())
        self.open_id = payload.get("openId")
        self.app_id = payload.get("appId")

    def usable(self) -> bool:
        return bool(self._token) and self.expires_at - time.time() > _RENEW_MARGIN

    def header(self) -> str:
        return f"Bearer {self._token}"

    def __repr__(self) -> str:  # Never shows the token.
        return f"<Credential {self.identity} {self.brand} expires in {max(0, int(self.expires_at - time.time()))}s>"


_credentials: dict[str, Credential] = {}
# Per event loop (a loop's objects cannot be used from another one).
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]]" = weakref.WeakKeyDictionary()


def _lock(identity: str) -> asyncio.Lock:
    locks = _locks.setdefault(asyncio.get_running_loop(), {})
    lock = locks.get(identity)
    if lock is None:
        lock = locks[identity] = asyncio.Lock()
    return lock


async def call_auth(action: str, timeout: float = 120.0, **args: Any) -> dict[str, Any]:
    """Runs one action of the plugin's ``lark_auth`` tool and returns its JSON reply."""
    import locus

    arguments = {"action": action, **{key: value for key, value in args.items() if value is not None}}
    result = await locus.call_tool(AUTH_TOOL, arguments, timeout=timeout)
    text = (result.output or "").strip()
    reply = _json_object(text)
    if result.is_error or not isinstance(reply, dict):
        message = reply.get("error") if isinstance(reply, dict) else None
        raise LarkAuthError(message or text or f"lark_auth {action} failed",
                            code=reply.get("code") if isinstance(reply, dict) else None)
    return reply


def _json_object(text: str) -> Any:
    """The JSON reply in a tool's output, also when the host wrapped it in other text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def credential(identity: str, *, rejected: Credential | None = None) -> Credential:
    """The access token for ``identity``; ``rejected`` is one the server
    refused, which is renewed unless another request already did."""
    current = _credentials.get(identity)
    if current is not None and current.usable() and current is not rejected:
        return current
    async with _lock(identity):
        current = _credentials.get(identity)
        if current is not None and current.usable() and current is not rejected:
            return current
        renew = rejected is not None
        # Another process may be renewing the same refresh token; the tool
        # then asks to retry, and the next call reads the stored result.
        for attempt in range(6):
            try:
                # renew: the server rejected the cached token, so the tool renews it even if it looks valid.
                reply = await call_auth("session", identity=identity, force=True if renew else None)
            except LarkAuthError as error:
                if error.code == "retry" and attempt < 5:
                    # Another process renewed it just now; take that token.
                    renew = False
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
                raise
            fresh = Credential(reply)
            _credentials[identity] = fresh
            return fresh
    raise LarkAuthError("Could not get an access token")


def forget_credentials() -> None:
    """Drops cached tokens, e.g. after signing in again."""
    _credentials.clear()


# ── HTTP ────────────────────────────────────────────────────────────────

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()


def http_client() -> "httpx.AsyncClient":
    """The keep-alive connection pool of the running event loop."""
    import httpx

    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=120),
            headers={"User-Agent": "locus-lark/0.1"},
            follow_redirects=True,
        )
        _clients[loop] = client
    return client


async def aclose() -> None:
    """Closes this loop's connection pool (optional; the process exit closes it too)."""
    client = _clients.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


def _clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _query(params: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in (params or {}).items():
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            if isinstance(item, bool):
                item = "true" if item else "false"
            pairs.append((key, str(item)))
    return pairs


def _violations(error: Mapping[str, Any]) -> list[str]:
    scopes = []
    for item in error.get("permission_violations") or []:
        subject = item.get("subject") if isinstance(item, Mapping) else None
        if subject:
            scopes.append(str(subject))
    return scopes


def _console_url(error: Mapping[str, Any]) -> str | None:
    for item in error.get("helps") or []:
        if isinstance(item, Mapping) and item.get("url"):
            return str(item["url"])
    return None


def enable_scopes_url(credential_: "Credential | None", scopes: list[str], identity: str) -> str | None:
    """The developer-console page that enables ``scopes`` on the app."""
    if credential_ is None or not credential_.app_id or not scopes:
        return None
    import base64
    import gzip
    from urllib.parse import quote

    side = "tenant" if identity == "bot" else "user"
    addons = {"scopes": {"tenant": [], "user": [], side: scopes}}
    raw = gzip.compress(json.dumps(addons, separators=(",", ":")).encode("utf-8"), mtime=0)
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{credential_.open_base}/page/launcher?clientID={quote(str(credential_.app_id))}&addons={encoded}"


def raise_for_reply(payload: Mapping[str, Any], status: int, identity: str, log_id: str | None,
                    credential_: "Credential | None" = None) -> None:
    code = payload.get("code")
    if code in (0, None):
        return
    message = str(payload.get("msg") or payload.get("message") or "request failed")
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    log_id = error.get("log_id") or log_id
    if code == _USER_SCOPE_ERROR:
        scopes = _violations(error)
        hint = f"; grant them with `await locus.lark.login(scopes={scopes!r})`" if scopes else ""
        raise LarkScopeError(f"The {identity} sign-in lacks permissions {', '.join(scopes) or ''}{hint} ({code}: {message})",
                             scopes=scopes, app_side=False, code=code, status=status, log_id=log_id, data=payload)
    if code == _APP_SCOPE_ERROR:
        scopes = _violations(error)
        url = _console_url(error) or enable_scopes_url(credential_, scopes, identity)
        where = f" at {url}" if url else " in the developer console"
        raise LarkScopeError(f"The app has not enabled {', '.join(scopes) or 'the needed permissions'}; enable them{where} and publish a new version ({code}: {message})",
                             scopes=scopes, app_side=True, console_url=url, code=code, status=status, log_id=log_id, data=payload)
    if code in _TOKEN_ERRORS:
        raise LarkAuthError(f"The {identity} access token was rejected ({code}: {message})",
                            code=code, status=status, log_id=log_id, data=payload)
    raise LarkError(f"{message} (code {code}{', log_id ' + log_id if log_id else ''})",
                    code=code, status=status, log_id=log_id, data=payload)


class Lark:
    """A Feishu / Lark client acting as ``identity``: ``"user"`` (the signed-in
    user, the default) or ``"bot"`` (the app's bot).

    Domain services are attributes, loaded on first use: ``chats``,
    ``messages``, ``users``, ``docs``, ``wiki``, ``drive``, ``sheets`` and
    ``base``; ``auth`` manages the sign-in and ``api`` makes raw requests to
    any endpoint. Objects it returns keep the client, so objects from
    ``lark.bot`` act as the bot.
    """

    def __init__(self, identity: str = "user") -> None:
        if identity not in ("user", "bot"):
            raise ValueError("identity must be 'user' or 'bot'")
        self.identity = identity

    def __repr__(self) -> str:
        return f"<locus.lark {self.identity}>"

    @property
    def user(self) -> "Lark":
        return self if self.identity == "user" else _client("user")

    @property
    def bot(self) -> "Lark":
        return self if self.identity == "bot" else _client("bot")

    def __getattr__(self, name: str) -> Any:
        service = _SERVICES.get(name)
        if service is None:
            raise AttributeError(f"'Lark' object has no attribute '{name}'")
        module_name, class_name = service
        import importlib

        module = importlib.import_module(f"{__package__}.{module_name}")
        value = getattr(module, class_name)(self)
        self.__dict__[name] = value
        return value

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(_SERVICES))

    async def open(self, link: str) -> Any:
        """The object behind a Feishu / Lark link: Document, Spreadsheet,
        Base, Folder, DriveFile, or the page behind a wiki link."""
        from ._drive import open_link

        return await open_link(self, link)

    # ── requests ────────────────────────────────────────────────────────

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """One request; returns the reply's ``data`` (``{}`` when there is
        none), or the body as bytes for a reply that is not JSON (downloads). ``path`` is a full URL
        or a path such as ``/open-apis/im/v1/chats``. Raises ``LarkError``."""
        import httpx

        credential_ = await credential(self.identity)
        url = path if path.startswith("http") else credential_.open_base + "/" + path.lstrip("/")
        body = _clean(json) if json is not None else None
        renewed = False
        for attempt in range(4):
            headers = {"Authorization": credential_.header()}
            try:
                response = await http_client().request(
                    method.upper(), url, params=_query(params), json=body, data=data, files=files,
                    headers=headers, timeout=timeout,
                )
            except httpx.HTTPError as error:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise LarkError(f"{method.upper()} {path}: {error}") from error
            log_id = response.headers.get("x-tt-logid")
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                if response.status_code in _RETRY_STATUS and attempt < 3:
                    await asyncio.sleep(_retry_delay(response, attempt))
                    continue
                if response.is_success:
                    return response.content
                raise LarkError(f"{method.upper()} {path}: HTTP {response.status_code}",
                                status=response.status_code, log_id=log_id, data=response.text[:2000])
            payload = response.json()
            code = payload.get("code") if isinstance(payload, dict) else None
            if code in _TOKEN_ERRORS and not renewed:
                renewed = True
                credential_ = await credential(self.identity, rejected=credential_)
                continue
            if (code in _RATE_LIMIT_ERRORS or response.status_code in _RETRY_STATUS) and attempt < 3:
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            if isinstance(payload, dict):
                raise_for_reply(payload, response.status_code, self.identity, log_id, credential_)
                result = payload.get("data")
                return result if result is not None else {}
            return payload
        raise LarkError(f"{method.upper()} {path}: gave up after retries")

    async def pages(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        items: str = "items",
        page_size: int | None = None,
        limit: int | None = None,
        token_in: str = "params",
    ) -> AsyncIterator[dict[str, Any]]:
        """Iterates the ``items`` of a paged list (``page_token`` /
        ``has_more``), one request per page, stopping after ``limit`` items.
        ``token_in="json"`` sends the page token and size in the body."""
        query = dict(params or {})
        body = dict(json) if json is not None else None
        target = body if token_in == "json" and body is not None else query
        if page_size:
            target["page_size"] = page_size if limit is None else min(page_size, max(1, limit))
        seen = 0
        while True:
            data = await self.request(method, path, params=query, json=body)
            for item in data.get(items) or []:
                yield item
                seen += 1
                if limit is not None and seen >= limit:
                    return
            token = data.get("page_token") or data.get("next_page_token")
            if not data.get("has_more") or not token:
                return
            target["page_token"] = token


def _retry_delay(response: Any, attempt: int) -> float:
    reset = response.headers.get("x-ogw-ratelimit-reset")
    try:
        if reset:
            return min(10.0, max(0.2, float(reset)))
    except ValueError:
        pass
    return 0.5 * 2**attempt


# Services served as client attributes: name -> (module, class).
_SERVICES = {
    "chats": ("_im", "Chats"),
    "messages": ("_im", "Messages"),
    "users": ("_contact", "Users"),
    "docs": ("_docs", "Docs"),
    "wiki": ("_wiki", "Wiki"),
    "drive": ("_drive", "Drive"),
    "sheets": ("_sheets", "Sheets"),
    "base": ("_bitable", "Bases"),
    "api": ("_raw", "Api"),
    "auth": ("_auth", "Auth"),
}

_by_identity: dict[str, Lark] = {}


def _client(identity: str = "user") -> Lark:
    client = _by_identity.get(identity)
    if client is None:
        client = _by_identity[identity] = Lark(identity)
    return client


# ── resources ───────────────────────────────────────────────────────────


class Resource:
    """Base of the objects the services return.

    ``raw`` is the platform's JSON for the object as last loaded; properties
    read from it. Objects compare equal by type and id, and keep the client
    (``lark``) they came from, so calls on them act as the same identity.
    """

    _id_key = "id"

    def __init__(self, lark: Lark, raw: Mapping[str, Any] | None = None, **extra: Any) -> None:
        self.lark = lark
        self.raw: dict[str, Any] = dict(raw or {})
        self.raw.update({key: value for key, value in extra.items() if value is not None})

    @property
    def id(self) -> str:
        return str(self.raw.get(self._id_key) or "")

    def _label(self) -> str:
        for key in ("name", "title", "summary"):
            value = self.raw.get(key)
            if value:
                return f" {str(value)[:40]!r}"
        return ""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.id}{self._label()}>"

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other.id == self.id  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.id))


class Service:
    """Base of the domain services; ``lark`` is the client they act for."""

    def __init__(self, lark: Lark) -> None:
        self.lark = lark

    def __repr__(self) -> str:
        return f"<locus.lark.{type(self).__name__.lower()} {self.lark.identity}>"

