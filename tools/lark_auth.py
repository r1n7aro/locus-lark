#!/usr/bin/env python3
"""``lark_auth``: the Feishu / Lark sign-in of the Locus plugin ``lark``.

Protocol (``locus-json``): stdin is ``{ args, settings, context }``; the last
stdout line is ``{ text, isError }`` whose ``text`` is a JSON reply. Writes
of the plugin's own settings (the app secret, the user token) go to stderr as
``@locus {"type":"setting",…}`` events, which Locus stores in the system
keychain. Only the Python standard library is used.

Actions:
- ``status``: app and sign-in state (no secrets).
- ``register``: creates a personal app in the user's tenant (device flow).
- ``login``: signs the user in (device flow) for scopes or domains.
- ``wait``: waits for the pending register / login to finish.
- ``cancel``: drops the pending flow.
- ``logout``: revokes and forgets the user token.
- ``forget_app``: forgets the app credentials and the sign-in.
- ``session``: a short-lived access token for ``locus.lark`` (user or bot).

The protocols follow the official SDK and CLI (github.com/larksuite/cli, MIT).
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
USER_AGENT = f"locus-lark/{VERSION}"
BRANDS = {
    "feishu": {"open": "https://open.feishu.cn", "accounts": "https://accounts.feishu.cn"},
    "lark": {"open": "https://open.larksuite.com", "accounts": "https://accounts.larksuite.com"},
}
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
# Renew user tokens this long before they expire.
RENEW_MARGIN = 300
DEFAULT_WAIT = 120
# A generation mismatch that lasts this long means the renewed token was not stored.
RENEW_SETTLE = 60

# Default user permissions per domain. The names follow the official lists
# (open.feishu.cn/lark-cli/apis/scopes.json); bitable:app is the permission of
# the Bitable v1 API that locus.lark uses.
DOMAIN_SCOPES: dict[str, list[str]] = {
    "wiki": [
        "wiki:space:read", "wiki:space:retrieve", "wiki:space:write_only", "wiki:node:read", "wiki:node:retrieve",
        "wiki:node:create", "wiki:node:move", "wiki:node:copy", "wiki:member:create", "wiki:member:retrieve",
        "wiki:member:update", "space:document:move",
    ],
    "docs": [
        "docx:document:readonly", "docx:document:create", "docx:document:write_only", "docs:document.media:upload",
        "docs:document.media:download", "search:docs:read",
    ],
    "drive": [
        "drive:drive.metadata:readonly", "drive:file:download", "drive:file:upload", "docs:document.content:read",
        "docs:document.comment:read", "docs:document.comment:create", "docs:document.comment:update",
        "docs:document.comment:delete", "docs:document.comment:write_only", "docs:document:copy",
        "docs:document:export", "docs:document:import", "docs:permission.member:create",
        "docs:permission.member:delete", "docs:permission.member:retrieve", "docs:permission.member:transfer",
        "docs:permission.setting:read", "docs:permission.setting:write_only", "space:document:delete",
        "space:document:move", "space:document:retrieve", "space:document:shortcut", "space:folder:create",
    ],
    "im": [
        "im:chat:read", "im:chat:create_by_user", "im:chat:update", "im:chat.members:read",
        "im:chat.members:write_only", "im:chat.managers:write_only", "im:message", "im:message:readonly",
        "im:message.group_msg:get_as_user", "im:message.p2p_msg:get_as_user", "im:message:recall",
        "im:message:update", "im:message.reactions:read", "im:message.reactions:write_only", "im:message.pins:read",
        "im:message.pins:write_only", "im:resource", "search:message",
    ],
    "contact": ["contact:user.basic_profile:readonly", "contact:user:search"],
    "sheets": [
        "sheets:spreadsheet:read", "sheets:spreadsheet:create", "sheets:spreadsheet:write_only",
        "sheets:spreadsheet.meta:read", "sheets:spreadsheet.meta:write_only",
    ],
    "base": ["bitable:app"],
}
DEFAULT_DOMAINS = list(DOMAIN_SCOPES)

# Permissions granted only when asked for: some tenants have an admin review
# them (the official CLI withholds im:message.send_as_user from bulk sign-ins
# for this reason), and the app may need to enable them first.
OPTIONAL_SCOPES: dict[str, dict[str, Any]] = {
    "send_as_user": {"label": "以你的身份发送、回复消息", "scopes": ["im:message.send_as_user"]},
    "chat_admin": {"label": "解散群、读写群公告、群置顶", "scopes": [
        "im:chat:delete", "im:chat.announcement:read", "im:chat.announcement:write_only",
        "im:chat.top_notice:write_only"]},
    "contact_lookup": {"label": "按邮箱或手机号查找用户", "scopes": ["contact:user.id:readonly"]},
    "wiki_rename": {"label": "重命名知识库节点", "scopes": ["wiki:node:update"]},
}


# Tests point every host at a local fixture server.
_TEST_ENDPOINT = os.environ.get("LOCUS_LARK_TEST_ENDPOINT", "").rstrip("/")
if _TEST_ENDPOINT:
    BRANDS = {brand: {"open": _TEST_ENDPOINT, "accounts": _TEST_ENDPOINT} for brand in BRANDS}


class ToolError(Exception):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


# ── protocol ────────────────────────────────────────────────────────────


def emit(event: dict[str, Any]) -> None:
    sys.stderr.buffer.write(("@locus " + json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stderr.flush()


def progress(message: str) -> None:
    emit({"type": "progress", "message": message})


def write_setting(name: str, value: Any) -> None:
    """Stores one of the plugin's toolWritable settings (secrets go to the keychain)."""
    emit({"type": "setting", "name": name, "value": value})


def reply(payload: dict[str, Any], is_error: bool = False) -> None:
    line = json.dumps({"text": json.dumps(payload, ensure_ascii=False), "isError": is_error}, ensure_ascii=False)
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


# ── state ───────────────────────────────────────────────────────────────


class State:
    """Settings from Locus plus the plugin's non-secret files in its data dir."""

    def __init__(self, payload: dict[str, Any]) -> None:
        settings = payload.get("settings") or {}
        context = payload.get("context") or {}
        self.brand = str(settings.get("brand") or "feishu")
        if self.brand not in BRANDS:
            self.brand = "feishu"
        self.app_id = str(settings.get("appId") or "").strip()
        self.app_secret = str(settings.get("appSecret") or "").strip()
        self.user_token = parse_token(settings.get("userToken"))
        self.data_dir = Path(context.get("pluginDataDir") or os.environ.get("LOCUS_PLUGIN_DATA_DIR") or ".")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def open_base(self) -> str:
        return BRANDS[self.brand]["open"]

    @property
    def accounts_base(self) -> str:
        return BRANDS[self.brand]["accounts"]

    def file(self, name: str) -> Path:
        return self.data_dir / name

    def read(self, name: str) -> dict[str, Any]:
        try:
            value = json.loads(self.file(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def write(self, name: str, value: dict[str, Any] | None) -> None:
        path = self.file(name)
        if value is None:
            with contextlib.suppress(OSError):
                path.unlink()
            return
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, path)

    def require_app(self) -> None:
        if not (self.app_id and self.app_secret):
            raise ToolError("No Feishu app is set up. Create one or enter an App ID and App Secret on the plugin's "
                            "Settings tab (Plugins → 飞书 / Lark).", "no_app")


def parse_token(value: Any) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        token = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, ValueError):
        return None
    return token if isinstance(token, dict) and token.get("access") else None


@contextlib.contextmanager
def file_lock(path: Path, timeout: float = 30.0):
    """A lock other tool processes of this plugin respect."""
    handle = open(path, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise ToolError("Another sign-in step is still running; try again.", "retry")
                time.sleep(0.1)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


# ── HTTP ────────────────────────────────────────────────────────────────


def http(method: str, url: str, *, form: dict[str, Any] | None = None, body: Any = None,
         headers: dict[str, str] | None = None, timeout: float = 30.0) -> tuple[int, dict[str, Any]]:
    data = None
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    if form is not None:
        data = urllib.parse.urlencode({key: value for key, value in form.items() if value is not None}).encode()
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ToolError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {error}", "network") from error
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        payload = {"error": "invalid_response", "error_description": raw[:300].decode("utf-8", "replace")}
    return status, payload if isinstance(payload, dict) else {"data": payload}


def oauth_error(payload: dict[str, Any]) -> str:
    return str(payload.get("error_description") or payload.get("msg") or payload.get("error") or "unknown error")


# ── scopes ──────────────────────────────────────────────────────────────


def requested_scopes(state: State, args: dict[str, Any]) -> list[str]:
    """The scopes of a sign-in: domains (all by default), optional groups,
    explicit scopes, and everything granted before (sign-ins add up)."""
    domains = args["domains"] if isinstance(args.get("domains"), list) else DEFAULT_DOMAINS
    unknown = [domain for domain in domains if domain not in DOMAIN_SCOPES]
    if unknown:
        raise ToolError(f"Unknown domains: {', '.join(unknown)}; use {', '.join(DOMAIN_SCOPES)}", "invalid_args")
    optional = args.get("optional") or []
    unknown = [name for name in optional if name not in OPTIONAL_SCOPES]
    if unknown:
        raise ToolError(f"Unknown optional permissions: {', '.join(unknown)}; use {', '.join(OPTIONAL_SCOPES)}",
                        "invalid_args")
    scopes: list[str] = []

    def add(items: Any) -> None:
        for scope in items:
            scope = str(scope).strip()
            if scope and scope not in scopes:
                scopes.append(scope)

    for domain in domains:
        add(DOMAIN_SCOPES[domain])
    for name in optional:
        add(OPTIONAL_SCOPES[name]["scopes"])
    extra = args.get("scopes") or []
    add(extra.split() if isinstance(extra, str) else extra)
    if state.user_token and state.user_token.get("appId") == state.app_id:
        add(str(state.user_token.get("scope") or "").split())
    if not scopes:
        raise ToolError("No permissions to request; name domains, optional groups or scopes.", "invalid_args")
    add(["offline_access"])
    return scopes


def addons_url(state: State, scopes: list[str], identity: str = "user") -> str | None:
    """The developer-console page that enables ``scopes`` on the app."""
    if not state.app_id or not scopes:
        return None
    addons = {"scopes": {"tenant": scopes if identity == "bot" else [], "user": [] if identity == "bot" else scopes}}
    return f"{state.open_base}/page/launcher?clientID={urllib.parse.quote(state.app_id)}&addons={encode_addons(addons)}"


def action_catalog(state: State, args: dict[str, Any]) -> dict[str, Any]:
    granted = set(str((state.user_token or {}).get("scope") or "").split())
    return {
        "domains": [{"id": domain, "scopes": scopes, "granted": all(scope in granted for scope in scopes)}
                    for domain, scopes in DOMAIN_SCOPES.items()],
        "optional": [{"id": name, "label": item["label"], "scopes": item["scopes"],
                      "granted": all(scope in granted for scope in item["scopes"])}
                     for name, item in OPTIONAL_SCOPES.items()],
    }


# ── app registration (device flow) ──────────────────────────────────────


def encode_addons(addons: dict[str, Any]) -> str:
    """JSON → gzip → base64url without padding, as the console pages expect."""
    payload = json.dumps(addons, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(gzip.compress(payload, mtime=0)).decode("ascii").rstrip("=")


def registration_url(state: State) -> str:
    # The registration protocol always starts on Feishu; Lark tenants switch while polling.
    return BRANDS["feishu"]["accounts"] + "/oauth/v1/app/registration"


def registration_scopes() -> list[str]:
    """User permissions a new app enables: the defaults (optional ones are
    enabled when first needed, so no admin review blocks creating the app)."""
    scopes: list[str] = []
    for items in DOMAIN_SCOPES.values():
        scopes.extend(scope for scope in items if scope not in scopes)
    return scopes


def action_register(state: State, args: dict[str, Any]) -> dict[str, Any]:
    url = registration_url(state)
    _, init = http("POST", url, form={"action": "init"})
    methods = init.get("supported_auth_methods") or []
    if methods and "client_secret" not in methods:
        raise ToolError("This tenant does not allow creating personal apps here; enter an existing app instead.",
                        "unsupported")
    status, begin = http("POST", url, form={"action": "begin", "archetype": "PersonalAgent",
                                            "auth_method": "client_secret", "request_user_info": "open_id tenant_brand"})
    if status >= 400 or begin.get("error") or not begin.get("device_code"):
        raise ToolError(f"Creating the app failed: {oauth_error(begin)}", "register_failed")
    verification = begin.get("verification_uri_complete") or begin.get("verification_uri") or ""
    parsed = urllib.parse.urlparse(verification)
    query = urllib.parse.parse_qs(parsed.query)
    query.update({"from": ["sdk"], "tp": ["sdk"], "source": ["python-sdk/locus"], "name": ["Locus"],
                  "desc": ["Locus 访问飞书知识库、文档与消息"],
                  "addons": [encode_addons({"scopes": {"tenant": [], "user": registration_scopes()}})]})
    link = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))
    expires_in = int(begin.get("expire_in") or begin.get("expires_in") or 600)
    pending = {"kind": "register", "deviceCode": begin["device_code"], "userCode": begin.get("user_code"),
               "url": link, "interval": int(begin.get("interval") or 5), "expiresAt": time.time() + expires_in,
               "pollBase": BRANDS["feishu"]["accounts"], "pollBrand": "feishu"}
    state.write("pending.json", pending)
    if args.get("open"):
        with contextlib.suppress(Exception):
            webbrowser.open(link)
    return {"url": link, "userCode": pending["userCode"], "expiresIn": expires_in, "kind": "register"}


def poll_register(state: State, pending: dict[str, Any]) -> dict[str, Any] | None:
    """One poll; the finished reply, ``None`` while waiting."""
    status, data = http("POST", pending["pollBase"] + "/oauth/v1/app/registration",
                        form={"action": "poll", "device_code": pending["deviceCode"]})
    if data.get("client_id") and data.get("client_secret"):
        brand = (data.get("user_info") or {}).get("tenant_brand") or pending.get("pollBrand") or "feishu"
        brand = brand if brand in BRANDS else "feishu"
        write_setting("brand", brand)
        write_setting("appId", data["client_id"])
        write_setting("appSecret", data["client_secret"])
        state.brand, state.app_id, state.app_secret = brand, data["client_id"], data["client_secret"]
        account = state.read("account.json")
        account.update({"appId": data["client_id"], "appSource": "registered", "brand": brand})
        state.write("account.json", account)
        if state.user_token:
            write_setting("userToken", None)
            state.user_token = None
        state.write("pending.json", None)
        return {"done": "registered", "appId": data["client_id"], "brand": brand}
    user_info = data.get("user_info") or {}
    if user_info.get("tenant_brand") == "lark" and pending.get("pollBrand") != "lark":
        pending["pollBase"], pending["pollBrand"] = BRANDS["lark"]["accounts"], "lark"
        state.write("pending.json", pending)
        return None
    error = data.get("error", "")
    # Pending replies may come with HTTP 400 (as in OAuth) or 200.
    if error in ("authorization_pending", "slow_down") or (not error and status < 400):
        if error == "slow_down":
            pending["interval"] = min(60, int(pending.get("interval", 5)) + 5)
            state.write("pending.json", pending)
        return None
    state.write("pending.json", None)
    if error == "access_denied":
        raise ToolError("The app was not created: the request was declined.", "denied")
    if error == "expired_token":
        raise ToolError("The link expired; start again.", "expired")
    raise ToolError(f"Creating the app failed: {oauth_error(data)}", "register_failed")


# ── user sign-in (device flow) ──────────────────────────────────────────


def basic_auth(state: State) -> str:
    return "Basic " + base64.b64encode(f"{state.app_id}:{state.app_secret}".encode()).decode()


def action_login(state: State, args: dict[str, Any]) -> dict[str, Any]:
    state.require_app()
    scopes = requested_scopes(state, args)
    status, data = http("POST", state.accounts_base + "/oauth/v1/device_authorization",
                        form={"client_id": state.app_id, "scope": " ".join(scopes)},
                        headers={"Authorization": basic_auth(state)})
    if status >= 400 or data.get("error") or not data.get("device_code"):
        message = f"Starting the sign-in failed: {oauth_error(data)}"
        extra = [scope for scope in scopes if scope not in registration_scopes() and scope != "offline_access"]
        url = addons_url(state, extra) if "scope" in json.dumps(data).lower() else None
        if url:
            message += f". The app may first need to enable {', '.join(extra)}: {url}"
        raise ToolError(message, "login_failed")
    link = data.get("verification_uri_complete") or data.get("verification_uri") or ""
    expires_in = int(data.get("expires_in") or 240)
    pending = {"kind": "login", "deviceCode": data["device_code"], "userCode": data.get("user_code"), "url": link,
               "interval": int(data.get("interval") or 5), "expiresAt": time.time() + expires_in, "scopes": scopes}
    state.write("pending.json", pending)
    if args.get("open"):
        with contextlib.suppress(Exception):
            webbrowser.open(link)
    return {"url": link, "userCode": pending["userCode"], "expiresIn": expires_in, "kind": "login",
            "scopes": scopes}


def store_user_token(state: State, data: dict[str, Any], generation: int) -> dict[str, Any]:
    now = time.time()
    token = {
        "v": 1, "appId": state.app_id, "brand": state.brand, "generation": generation,
        "access": data["access_token"], "refresh": data.get("refresh_token") or "",
        "expiresAt": now + int(data.get("expires_in") or 7200),
        "refreshExpiresAt": now + int(data.get("refresh_token_expires_in") or data.get("refresh_expires_in") or 604800),
        "scope": data.get("scope") or "",
    }
    write_setting("userToken", json.dumps(token))
    state.user_token = token
    account = state.read("account.json")
    account.update({"appId": state.app_id, "brand": state.brand, "generation": generation, "scope": token["scope"],
                    "expiresAt": token["expiresAt"], "refreshExpiresAt": token["refreshExpiresAt"], "renewedAt": now})
    state.write("account.json", account)
    return token


def poll_login(state: State, pending: dict[str, Any]) -> dict[str, Any] | None:
    state.require_app()
    status, data = http("POST", state.accounts_base + "/oauth/v3/token", form={
        "grant_type": DEVICE_GRANT, "device_code": pending["deviceCode"], "client_id": state.app_id,
        "client_secret": state.app_secret})
    error = data.get("error", "")
    if not error and data.get("access_token"):
        generation = int(state.read("account.json").get("generation") or 0) + 1
        token = store_user_token(state, data, generation)
        user = fetch_user(state, token["access"])
        account = state.read("account.json")
        account["user"] = user
        state.write("account.json", account)
        state.write("pending.json", None)
        return {"done": "signedIn", "user": user, "scope": token["scope"]}
    if error in ("authorization_pending", "slow_down"):
        if error == "slow_down":
            pending["interval"] = min(60, int(pending.get("interval", 5)) + 5)
            state.write("pending.json", pending)
        return None
    state.write("pending.json", None)
    if error == "access_denied":
        raise ToolError("The sign-in was declined.", "denied")
    if error in ("expired_token", "invalid_grant"):
        raise ToolError("The sign-in link expired; start again.", "expired")
    raise ToolError(f"Signing in failed: {oauth_error(data)}", "login_failed")


def fetch_user(state: State, access_token: str) -> dict[str, Any]:
    _, data = http("GET", state.open_base + "/open-apis/authen/v1/user_info",
                   headers={"Authorization": f"Bearer {access_token}"})
    info = data.get("data") or {}
    return {"name": info.get("name") or info.get("en_name"), "openId": info.get("open_id"),
            "avatarUrl": info.get("avatar_url") or info.get("avatar_thumb"), "tenantKey": info.get("tenant_key"),
            "email": info.get("email") or info.get("enterprise_email")}


def action_wait(state: State, args: dict[str, Any]) -> dict[str, Any]:
    timeout = float(args.get("timeoutSeconds") or DEFAULT_WAIT)
    deadline = time.monotonic() + timeout
    while True:
        pending = state.read("pending.json")
        if not pending:
            raise ToolError("Nothing is waiting; start with register or login.", "no_pending")
        if time.time() > float(pending.get("expiresAt") or 0):
            state.write("pending.json", None)
            raise ToolError("The link expired; start again.", "expired")
        result = poll_register(state, pending) if pending.get("kind") == "register" else poll_login(state, pending)
        if result is not None:
            result["status"] = status_reply(state)
            return result
        if time.monotonic() > deadline:
            return {"done": None, "waiting": True, "status": status_reply(state)}
        progress("Waiting for confirmation in Feishu")
        time.sleep(max(1, int(pending.get("interval") or 5)))


# ── tokens for locus.lark ───────────────────────────────────────────────


def refresh_user_token(state: State, force: bool = False) -> dict[str, Any]:
    """A fresh user token; renews it (once across processes) when it is
    about to expire, or with ``force`` when the server rejected it."""
    token = state.user_token
    if not token or token.get("appId") != state.app_id:
        raise ToolError("Not signed in to Feishu. Sign in on the plugin's Settings tab, or run "
                        "`await locus.lark.login()`.", "not_signed_in")
    if not force and float(token.get("expiresAt") or 0) - time.time() > RENEW_MARGIN:
        return token
    if not token.get("refresh") or float(token.get("refreshExpiresAt") or 0) < time.time():
        sign_out(state)
        raise ToolError("The Feishu sign-in expired; sign in again.", "expired")
    with file_lock(state.file("refresh.lock")):
        account = state.read("account.json")
        if int(account.get("generation") or 0) > int(token.get("generation") or 0):
            if time.time() - float(account.get("renewedAt") or 0) < RENEW_SETTLE:
                # Another process renewed it; the next call reads the new token.
                raise ToolError("The token was just renewed; retry.", "retry")
            sign_out(state)
            raise ToolError("The renewed Feishu sign-in was not stored; sign in again.", "expired")
        status, data = http("POST", state.accounts_base + "/oauth/v3/token", body={
            "grant_type": "refresh_token", "refresh_token": token["refresh"], "client_id": state.app_id,
            "client_secret": state.app_secret})
        if data.get("access_token"):
            return store_user_token(state, data, int(token.get("generation") or 0) + 1)
        if not force and float(token.get("expiresAt") or 0) > time.time() + 30:
            return token  # Still usable; try renewing next time.
        sign_out(state)
        raise ToolError(f"The Feishu sign-in could not be renewed ({oauth_error(data)}); sign in again.", "expired")


def tenant_token(state: State) -> dict[str, Any]:
    state.require_app()
    _, data = http("POST", state.open_base + "/open-apis/auth/v3/tenant_access_token/internal",
                   body={"app_id": state.app_id, "app_secret": state.app_secret})
    if data.get("code") not in (0, None) or not data.get("tenant_access_token"):
        raise ToolError(f"The app credentials were rejected: {data.get('msg') or oauth_error(data)}", "bad_app")
    return {"access": data["tenant_access_token"], "expiresAt": time.time() + int(data.get("expire") or 7200)}


def action_session(state: State, args: dict[str, Any]) -> dict[str, Any]:
    identity = args.get("identity") or "user"
    if identity == "bot":
        token = tenant_token(state)
        account = {}
    else:
        state.require_app()
        token = refresh_user_token(state, force=bool(args.get("force")))
        account = state.read("account.json")
    return {"identity": identity, "accessToken": token["access"], "expiresAt": token["expiresAt"],
            "openBase": state.open_base, "brand": state.brand, "appId": state.app_id,
            "scope": token.get("scope", ""), "openId": (account.get("user") or {}).get("openId")}


# ── status, logout ──────────────────────────────────────────────────────


def sign_out(state: State) -> None:
    write_setting("userToken", None)
    state.user_token = None
    account = state.read("account.json")
    for key in ("user", "scope", "expiresAt", "refreshExpiresAt"):
        account.pop(key, None)
    state.write("account.json", account)


def status_reply(state: State) -> dict[str, Any]:
    account = state.read("account.json")
    token = state.user_token
    signed_in = bool(token and token.get("appId") == state.app_id and state.app_id
                     and float(token.get("refreshExpiresAt") or 0) > time.time())
    pending = state.read("pending.json")
    if pending and time.time() > float(pending.get("expiresAt") or 0):
        pending = {}
    result: dict[str, Any] = {
        "brand": state.brand, "appReady": bool(state.app_id and state.app_secret), "appId": state.app_id or None,
        "appSource": account.get("appSource") if account.get("appId") == state.app_id else ("custom" if state.app_id else None),
        "signedIn": signed_in,
    }
    if signed_in:
        result.update({"user": account.get("user") or {}, "scope": token.get("scope", ""),
                       "expiresAt": token.get("expiresAt"), "refreshExpiresAt": token.get("refreshExpiresAt")})
    if pending:
        result["pending"] = {"kind": pending.get("kind"), "url": pending.get("url"), "userCode": pending.get("userCode"),
                             "expiresAt": pending.get("expiresAt")}
    return result


def action_logout(state: State, args: dict[str, Any]) -> dict[str, Any]:
    token = state.user_token
    if token and state.app_id and state.app_secret:
        with contextlib.suppress(ToolError):
            http("POST", state.accounts_base + "/oauth/v1/revoke", form={
                "client_id": state.app_id, "client_secret": state.app_secret, "token": token.get("refresh") or token.get("access"),
                "token_type_hint": "refresh_token" if token.get("refresh") else "access_token"}, timeout=10)
    sign_out(state)
    state.write("pending.json", None)
    return status_reply(state)


def action_forget_app(state: State, args: dict[str, Any]) -> dict[str, Any]:
    action_logout(state, args)
    write_setting("appSecret", None)
    write_setting("appId", None)
    state.app_id = state.app_secret = ""
    state.write("account.json", None)
    return status_reply(state)


def action_cancel(state: State, args: dict[str, Any]) -> dict[str, Any]:
    state.write("pending.json", None)
    return status_reply(state)


ACTIONS = {
    "status": lambda state, args: status_reply(state),
    "catalog": action_catalog,
    "register": action_register,
    "login": action_login,
    "wait": action_wait,
    "cancel": action_cancel,
    "logout": action_logout,
    "forget_app": action_forget_app,
    "session": action_session,
}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError:
        reply({"error": "invalid input"}, True)
        return
    args = payload.get("args") or {}
    action = str(args.get("action") or "status")
    handler = ACTIONS.get(action)
    if handler is None:
        reply({"error": f"Unknown action {action!r}; use {', '.join(ACTIONS)}", "code": "invalid_args"}, True)
        return
    try:
        state = State(payload)
        reply(handler(state, args))
    except ToolError as error:
        reply({"error": str(error), "code": error.code}, True)


if __name__ == "__main__":
    main()
