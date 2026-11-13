"""lark_auth against the fake accounts service: app creation, sign-in,
sessions, renewal across processes, sign-out. No real Feishu is involved."""

from __future__ import annotations

import base64
import gzip
import json
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

from fake_lark import APP_ID, APP_SECRET, FakeHost, FakeLark


class AuthToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeLark()
        self.temp = tempfile.TemporaryDirectory()
        self.host = FakeHost(self.fake.url, Path(self.temp.name))

    def tearDown(self) -> None:
        self.fake.close()
        self.temp.cleanup()

    def sign_in(self) -> dict:
        self.host.settings.update({"appId": APP_ID, "appSecret": APP_SECRET, "brand": "feishu"})
        reply, is_error = self.host.run({"action": "login"})
        self.assertFalse(is_error, reply)
        reply, is_error = self.host.run({"action": "wait", "timeoutSeconds": 30})
        self.assertFalse(is_error, reply)
        return reply

    def test_status_without_app(self) -> None:
        reply, is_error = self.host.run({"action": "status"})
        self.assertFalse(is_error)
        self.assertEqual(reply["appReady"], False)
        self.assertEqual(reply["signedIn"], False)
        reply, is_error = self.host.run({"action": "session"})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "no_app")

    def test_register_creates_app_and_stores_secret(self) -> None:
        reply, is_error = self.host.run({"action": "register"})
        self.assertFalse(is_error, reply)
        self.assertEqual(reply["userCode"], "REG1")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(reply["url"]).query)
        self.assertEqual(query["user_code"], ["REG1"])
        self.assertEqual(query["source"], ["python-sdk/locus"])
        addons = json.loads(gzip.decompress(base64.urlsafe_b64decode(query["addons"][0] + "==")))
        self.assertIn("im:message:readonly", addons["scopes"]["user"])
        # Optional permissions are not preset on a new app.
        self.assertNotIn("im:message.send_as_user", addons["scopes"]["user"])

        status, _ = self.host.run({"action": "status"})
        self.assertEqual(status["pending"]["kind"], "register")

        reply, is_error = self.host.run({"action": "wait", "timeoutSeconds": 30})
        self.assertFalse(is_error, reply)
        self.assertEqual(reply["done"], "registered")
        self.assertEqual(self.host.settings["appId"], APP_ID)
        self.assertEqual(self.host.settings["appSecret"], APP_SECRET)
        self.assertEqual(self.host.settings["brand"], "feishu")
        self.assertTrue(reply["status"]["appReady"])
        self.assertEqual(reply["status"]["appSource"], "registered")
        self.assertNotIn("pending", reply["status"])
        # Secrets never appear in replies.
        self.assertNotIn(APP_SECRET, json.dumps(reply))

    def test_register_switches_to_lark(self) -> None:
        self.fake.tenant_brand = "lark"
        self.host.run({"action": "register"})
        reply, is_error = self.host.run({"action": "wait", "timeoutSeconds": 30})
        self.assertFalse(is_error, reply)
        self.assertEqual(self.host.settings["brand"], "lark")

    def test_login_session_and_status(self) -> None:
        reply = self.sign_in()
        self.assertEqual(reply["done"], "signedIn")
        self.assertEqual(reply["user"]["name"], "测试用户")
        token = json.loads(self.host.settings["userToken"])
        self.assertEqual(token["access"], "u-at-1")
        self.assertEqual(token["generation"], 1)
        # Default domains, no optional group, offline_access added.
        scopes = self.fake.granted_scope.split()
        self.assertIn("wiki:node:read", scopes)
        self.assertIn("bitable:app", scopes)
        self.assertIn("offline_access", scopes)
        self.assertNotIn("im:message.send_as_user", scopes)

        session, is_error = self.host.run({"action": "session"})
        self.assertFalse(is_error, session)
        self.assertEqual(session["accessToken"], "u-at-1")
        self.assertEqual(session["openId"], "ou_me")
        self.assertEqual(session["openBase"], self.fake.url)
        self.assertNotIn("u-rt-1", json.dumps(session))

        bot, is_error = self.host.run({"action": "session", "identity": "bot"})
        self.assertFalse(is_error, bot)
        self.assertEqual(bot["accessToken"], "t-at")

        status, _ = self.host.run({"action": "status"})
        self.assertTrue(status["signedIn"])
        self.assertEqual(status["user"]["openId"], "ou_me")
        self.assertNotIn("u-at-1", json.dumps(status))

    def test_incremental_login_keeps_granted_scopes(self) -> None:
        self.sign_in()
        self.host.run({"action": "login", "domains": [], "optional": ["send_as_user"]})
        scopes = self.fake.granted_scope.split()
        self.assertIn("im:message.send_as_user", scopes)
        self.assertIn("docx:document:readonly", scopes)   # kept from the first sign-in

        catalog, _ = self.host.run({"action": "catalog"})
        self.assertTrue(all(item["granted"] for item in catalog["domains"]))
        self.assertFalse(next(item for item in catalog["optional"] if item["id"] == "send_as_user")["granted"])

    def test_invalid_login_arguments(self) -> None:
        self.host.settings.update({"appId": APP_ID, "appSecret": APP_SECRET})
        reply, is_error = self.host.run({"action": "login", "domains": ["calendar"]})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "invalid_args")
        reply, is_error = self.host.run({"action": "login", "domains": [], "optional": []})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "invalid_args")

    def test_rejected_scopes_link_to_the_console(self) -> None:
        self.host.settings.update({"appId": APP_ID, "appSecret": APP_SECRET})
        self.fake.reject_scopes = True
        reply, is_error = self.host.run({"action": "login", "optional": ["chat_admin"]})
        self.assertTrue(is_error)
        self.assertIn("/page/launcher?clientID=cli_test&addons=", reply["error"])
        self.assertIn("im:chat:delete", reply["error"])

    def test_session_renews_an_expiring_token(self) -> None:
        self.sign_in()
        token = json.loads(self.host.settings["userToken"])
        token["expiresAt"] = time.time() + 60          # inside the renewal margin
        self.host.settings["userToken"] = json.dumps(token)
        session, is_error = self.host.run({"action": "session"})
        self.assertFalse(is_error, session)
        self.assertEqual(session["accessToken"], "u-at-2")
        renewed = json.loads(self.host.settings["userToken"])
        self.assertEqual((renewed["refresh"], renewed["generation"]), ("u-rt-2", 2))

    def test_stale_process_is_told_to_retry(self) -> None:
        self.sign_in()
        token = json.loads(self.host.settings["userToken"])
        token["expiresAt"] = time.time() + 60
        self.host.settings["userToken"] = json.dumps(token)
        stale = dict(self.host.settings)
        first, _ = self.host.run({"action": "session"})           # renews: generation 2
        self.assertEqual(first["accessToken"], "u-at-2")
        reply, is_error = self.host.run({"action": "session"}, settings=stale)  # started before the write
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "retry")
        again, is_error = self.host.run({"action": "session"})    # current settings
        self.assertFalse(is_error)
        self.assertEqual(again["accessToken"], "u-at-2")

    def test_lost_renewal_signs_out(self) -> None:
        self.sign_in()
        token = json.loads(self.host.settings["userToken"])
        token["expiresAt"] = time.time() + 10
        self.host.settings["userToken"] = json.dumps(token)
        account_file = Path(self.temp.name) / "account.json"
        account = json.loads(account_file.read_text(encoding="utf-8"))
        account.update({"generation": 5, "renewedAt": time.time() - 3600})
        account_file.write_text(json.dumps(account), encoding="utf-8")
        reply, is_error = self.host.run({"action": "session"})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "expired")
        self.assertNotIn("userToken", self.host.settings)

    def test_logout_revokes_and_forget_app_clears_everything(self) -> None:
        self.sign_in()
        reply, is_error = self.host.run({"action": "logout"})
        self.assertFalse(is_error)
        self.assertFalse(reply["signedIn"])
        self.assertEqual(self.fake.revoked, ["u-rt-1"])
        self.assertNotIn("userToken", self.host.settings)
        self.assertEqual(self.host.settings["appId"], APP_ID)

        reply, is_error = self.host.run({"action": "forget_app"})
        self.assertFalse(is_error)
        self.assertFalse(reply["appReady"])
        self.assertNotIn("appSecret", self.host.settings)
        self.assertNotIn("appId", self.host.settings)

    def test_cancel_drops_the_pending_flow(self) -> None:
        self.host.run({"action": "register"})
        reply, _ = self.host.run({"action": "cancel"})
        self.assertNotIn("pending", reply)
        reply, is_error = self.host.run({"action": "wait", "timeoutSeconds": 1})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "no_pending")

    def test_wait_times_out_without_failing(self) -> None:
        self.fake.registration_pending = 100
        self.host.run({"action": "register"})
        reply, is_error = self.host.run({"action": "wait", "timeoutSeconds": 1})
        self.assertFalse(is_error)
        self.assertTrue(reply["waiting"])
        self.assertTrue(any(event.get("type") == "progress" for event in self.host.events))

    def test_unknown_action(self) -> None:
        reply, is_error = self.host.run({"action": "nope"})
        self.assertTrue(is_error)
        self.assertEqual(reply["code"], "invalid_args")


if __name__ == "__main__":
    unittest.main()
