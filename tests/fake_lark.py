"""A local stand-in for the Feishu Open Platform and accounts service, plus a
stand-in for the Locus host that runs the plugin's ``lark_auth`` tool.

Nothing here talks to the real Feishu: the tool and the SDK are pointed at
the fake server through ``LOCUS_LARK_TEST_ENDPOINT`` and the session reply.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import types
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "lark_auth.py"
APP_ID, APP_SECRET = "cli_test", "sec_test"


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    @property
    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except ValueError:
            return None

    @property
    def form(self) -> dict[str, str]:
        return {key: values[-1] for key, values in urllib.parse.parse_qs(self.body.decode("utf-8")).items()}

    def arg(self, name: str, default: Any = None) -> Any:
        values = self.query.get(name)
        return values[-1] if values else default

    @property
    def token(self) -> str | None:
        value = self.headers.get("authorization", "")
        return value[7:] if value.startswith("Bearer ") else None


Reply = tuple  # (status, body: dict | bytes, headers?)
Handler = Callable[[Request], Any]


@dataclass
class FakeLark:
    """Accounts + Open Platform. ``route()`` adds API handlers; a handler
    returns a dict (wrapped as ``{"code": 0, "data": …}`` unless it has a
    ``code``), bytes, or ``(status, body)``."""

    registration_pending: int = 1
    login_pending: int = 1
    tenant_brand: str = "feishu"
    reject_scopes: bool = False
    requests: list[Request] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    valid_tokens: set[str] = field(default_factory=set)
    routes: dict[tuple[str, str], Handler] = field(default_factory=dict)
    granted_scope: str = ""

    def __post_init__(self) -> None:
        self._polls = {"register": 0, "login": 0}
        self._generation = 0
        self.refresh_token: str | None = None
        self.valid_tokens.add("t-at")
        fake = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _serve(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                length = int(self.headers.get("content-length") or 0)
                request = Request(self.command, parsed.path, urllib.parse.parse_qs(parsed.query),
                                  {key.lower(): value for key, value in self.headers.items()},
                                  self.rfile.read(length) if length else b"")
                fake.requests.append(request)
                status, body, headers = fake.dispatch(request)
                raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", headers.pop("content-type", "application/octet-stream" if isinstance(body, bytes) else "application/json; charset=utf-8"))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self.routes[(method.upper(), path)] = handler
            return handler

        return register

    def calls(self, method: str, path: str) -> list[Request]:
        return [request for request in self.requests if request.method == method and request.path == path]

    def expire(self, token: str) -> None:
        self.valid_tokens.discard(token)

    # ── dispatch ────────────────────────────────────────────────────────

    def dispatch(self, request: Request) -> tuple[int, Any, dict[str, str]]:
        auth = {
            ("POST", "/oauth/v1/app/registration"): self._registration,
            ("POST", "/oauth/v1/device_authorization"): self._device_authorization,
            ("POST", "/oauth/v3/token"): self._token,
            ("POST", "/oauth/v1/revoke"): self._revoke,
            ("POST", "/open-apis/auth/v3/tenant_access_token/internal"): self._tenant_token,
        }.get((request.method, request.path))
        if auth is not None:
            return self._normalize(auth(request))
        if request.token not in self.valid_tokens:
            return 401, {"code": 99991663, "msg": "Invalid access token for authorization"}, {}
        if request.method == "GET" and request.path == "/open-apis/authen/v1/user_info":
            return 200, {"code": 0, "data": {"name": "测试用户", "open_id": "ou_me", "avatar_url": "https://a/x.png",
                                             "tenant_key": "tk"}}, {}
        handler = self.routes.get((request.method, request.path))
        if handler is None:
            for (method, pattern), candidate in self.routes.items():
                if method == request.method and _match(pattern, request.path):
                    handler = candidate
                    break
        if handler is None:
            return 404, {"code": 1254040, "msg": f"no fake route {request.method} {request.path}"}, {}
        return self._normalize(handler(request), wrap=True)

    @staticmethod
    def _normalize(result: Any, wrap: bool = False) -> tuple[int, Any, dict[str, str]]:
        if isinstance(result, tuple):
            status, body, *rest = result
            return status, body, dict(rest[0]) if rest else {}
        if isinstance(result, bytes):
            return 200, result, {}
        if wrap and (not isinstance(result, dict) or "code" not in result):
            return 200, {"code": 0, "msg": "success", "data": result if result is not None else {}}, {}
        return 200, result, {}

    # ── accounts ────────────────────────────────────────────────────────

    def _registration(self, request: Request) -> Any:
        form = request.form
        action = form.get("action")
        if action == "init":
            return {"supported_auth_methods": ["client_secret"]}
        if action == "begin":
            assert form.get("archetype") == "PersonalAgent", form
            return {"device_code": "reg-dc", "user_code": "REG1", "verification_uri": f"{self.url}/page/reg",
                    "verification_uri_complete": f"{self.url}/page/reg?user_code=REG1", "expire_in": 600, "interval": 1}
        if action == "poll":
            self._polls["register"] += 1
            if self._polls["register"] <= self.registration_pending:
                return 400, {"error": "authorization_pending"}
            return {"client_id": APP_ID, "client_secret": APP_SECRET,
                    "user_info": {"open_id": "ou_me", "tenant_brand": self.tenant_brand}}
        return 400, {"error": "invalid_request"}

    def _basic_ok(self, request: Request) -> bool:
        expected = "Basic " + base64.b64encode(f"{APP_ID}:{APP_SECRET}".encode()).decode()
        return request.headers.get("authorization") == expected

    def _device_authorization(self, request: Request) -> Any:
        if not self._basic_ok(request):
            return 401, {"error": "invalid_client", "error_description": "bad app credentials"}
        if self.reject_scopes:
            return 400, {"error": "invalid_scope", "error_description": "scope not enabled for this app"}
        self.granted_scope = request.form.get("scope", "")
        return {"device_code": "dev-dc", "user_code": "DEV1", "verification_uri": f"{self.url}/page/dev",
                "verification_uri_complete": f"{self.url}/page/dev?user_code=DEV1", "expires_in": 240, "interval": 1}

    def _issue(self) -> dict[str, Any]:
        self._generation += 1
        access, refresh = f"u-at-{self._generation}", f"u-rt-{self._generation}"
        self.valid_tokens.add(access)
        self.refresh_token = refresh
        return {"access_token": access, "refresh_token": refresh, "expires_in": 7200,
                "refresh_token_expires_in": 604800, "scope": self.granted_scope, "token_type": "Bearer"}

    def _token(self, request: Request) -> Any:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = request.json or {}
            if body.get("grant_type") != "refresh_token" or body.get("client_secret") != APP_SECRET:
                return 400, {"error": "invalid_request"}
            if body.get("refresh_token") != self.refresh_token:
                return 400, {"error": "invalid_grant", "error_description": "refresh token reused or revoked"}
            return self._issue()
        form = request.form
        if form.get("client_secret") != APP_SECRET or form.get("device_code") != "dev-dc":
            return 400, {"error": "invalid_grant"}
        self._polls["login"] += 1
        if self._polls["login"] <= self.login_pending:
            return 400, {"error": "authorization_pending"}
        return self._issue()

    def _revoke(self, request: Request) -> Any:
        self.revoked.append(request.form.get("token", ""))
        return {}

    def _tenant_token(self, request: Request) -> Any:
        body = request.json or {}
        if body.get("app_id") != APP_ID or body.get("app_secret") != APP_SECRET:
            return {"code": 10014, "msg": "app secret invalid"}
        return {"code": 0, "msg": "ok", "tenant_access_token": "t-at", "expire": 7200}


def _match(pattern: str, path: str) -> bool:
    """``/a/:id/b`` style patterns."""
    left, right = pattern.strip("/").split("/"), path.strip("/").split("/")
    return len(left) == len(right) and all(a.startswith(":") or a == b for a, b in zip(left, right))


# ── the Locus host side ─────────────────────────────────────────────────


class FakeHost:
    """Runs ``lark_auth`` like Locus does: settings on stdin, setting events
    on stderr stored back, the last stdout line as the result."""

    def __init__(self, endpoint: str, data_dir: Path, settings: dict[str, Any] | None = None) -> None:
        self.endpoint = endpoint
        self.data_dir = data_dir
        self.settings: dict[str, Any] = dict(settings or {})
        self.events: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def run(self, args: dict[str, Any], settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        payload = {"args": args, "settings": dict(self.settings if settings is None else settings),
                   "context": {"caller": "agent", "pluginDataDir": str(self.data_dir)}}
        env = {**os.environ, "LOCUS_LARK_TEST_ENDPOINT": self.endpoint, "PYTHONIOENCODING": "utf-8"}
        env.pop("LOCUS_PLUGIN_DATA_DIR", None)
        process = subprocess.run([sys.executable, str(TOOL)], input=json.dumps(payload).encode("utf-8"),
                                 capture_output=True, env=env, timeout=60)
        self.calls.append(args)
        for line in process.stderr.decode("utf-8", "replace").splitlines():
            if not line.startswith("@locus "):
                continue
            event = json.loads(line[7:])
            self.events.append(event)
            if event.get("type") == "setting":
                if event.get("value") is None:
                    self.settings.pop(event["name"], None)
                else:
                    self.settings[event["name"]] = event["value"]
        lines = [line for line in process.stdout.decode("utf-8").splitlines() if line.strip()]
        if not lines:
            raise AssertionError(f"lark_auth printed nothing: {process.stderr.decode('utf-8', 'replace')}")
        result = json.loads(lines[-1])
        return json.loads(result["text"]), bool(result["isError"])

    def setting_writes(self) -> list[str]:
        return [event["name"] for event in self.events if event.get("type") == "setting"]

    def locus_module(self) -> types.ModuleType:
        """A ``locus`` module whose ``call_tool`` runs the tool through this host."""
        import asyncio

        host = self
        module = types.ModuleType("locus")

        async def call_tool(name: str, arguments: dict[str, Any] | None = None, *, timeout: float | None = None,
                            **_: Any) -> Any:
            assert name == "lark_auth", name
            reply, is_error = await asyncio.to_thread(host.run, dict(arguments or {}))
            return types.SimpleNamespace(name=name, output=json.dumps(reply, ensure_ascii=False), is_error=is_error)

        module.call_tool = call_tool  # type: ignore[attr-defined]
        return module


class SignedInCase:
    """Mixin for unittest cases: a fake server, a signed-in fake host, and
    ``locus_lark`` with ``locus.call_tool`` routed through that host."""

    fake: FakeLark
    host: FakeHost

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls.temp = tempfile.TemporaryDirectory()
        cls.fake = FakeLark()
        cls.host = FakeHost(cls.fake.url, Path(cls.temp.name),
                            {"appId": APP_ID, "appSecret": APP_SECRET, "brand": "feishu"})
        cls.host.run({"action": "login"})
        reply, is_error = cls.host.run({"action": "wait", "timeoutSeconds": 30})
        assert not is_error, reply
        sys.modules["locus"] = cls.host.locus_module()
        import locus_lark

        cls.lark = locus_lark

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("locus", None)
        cls.fake.close()
        cls.temp.cleanup()

    def setUp(self) -> None:
        from locus_lark import _core

        _core.forget_credentials()
        self.fake.routes.clear()
        self.fake.requests.clear()
        self.host.calls.clear()

    def run_async(self, coro: Any) -> Any:
        import asyncio

        return asyncio.run(coro)

    def body(self, method: str, path: str, index: int = -1) -> Any:
        return self.fake.calls(method, path)[index].json

    def ok(self, method: str, path: str, reply: Any = None) -> None:
        """A route that answers ``reply`` (``{}`` by default)."""
        self.fake.route(method, path)(lambda request: {} if reply is None else (reply(request) if callable(reply) else reply))
