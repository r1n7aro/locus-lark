"""locus.lark against the fake Open Platform, with tokens from the real
lark_auth tool run through a fake Locus host."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fake_lark import ROOT, SignedInCase


class LazyImportTest(unittest.TestCase):
    def test_import_loads_neither_httpx_nor_services(self) -> None:
        code = (
            "import sys, locus_lark\n"
            "assert 'httpx' not in sys.modules\n"
            "locus_lark.chats; locus_lark.Document\n"
            "assert 'httpx' not in sys.modules, 'httpx'\n"
            "assert 'locus_lark._bitable' not in sys.modules and 'locus_lark._wiki' not in sys.modules\n"
            "assert locus_lark.text_block('x')['block_type'] == 2\n"
            "print('ok')\n"
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "python"), str(ROOT / ".test-deps")])}
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.stdout.strip(), "ok", result.stderr)


class SdkTest(SignedInCase, unittest.TestCase):
    # ── client mechanics ────────────────────────────────────────────────

    def test_one_session_serves_concurrent_requests(self) -> None:
        @self.fake.route("GET", "/open-apis/im/v1/chats/:id")
        def chat(request):
            return {"chat_id": request.path.rsplit("/", 1)[-1], "name": "群"}

        async def main():
            chats = await asyncio.gather(*(self.lark.chats.get(f"oc_{n}") for n in range(5)))
            return [chat.id for chat in chats]

        self.assertEqual(self.run_async(main()), [f"oc_{n}" for n in range(5)])
        self.assertEqual([call["action"] for call in self.host.calls], ["session"])
        self.assertTrue(all(r.token == "u-at-1" or r.token.startswith("u-at") for r in self.fake.calls("GET", "/open-apis/im/v1/chats/oc_0")))

    def test_rejected_token_is_renewed_once(self) -> None:
        @self.fake.route("GET", "/open-apis/im/v1/chats/oc_1")
        def chat(request):
            return {"chat_id": "oc_1", "name": "群"}

        async def main():
            await self.lark.chats.get("oc_1")                    # caches the current token
            current = json.loads(self.host.settings["userToken"])["access"]
            self.fake.expire(current)                            # e.g. revoked on the server
            chat = await self.lark.chats.get("oc_1")
            return current, chat

        old, chat = self.run_async(main())
        self.assertEqual(chat.name, "群")
        new = json.loads(self.host.settings["userToken"])["access"]
        self.assertNotEqual(old, new)
        self.assertEqual([call.get("force") for call in self.host.calls], [None, True])

    def test_rate_limits_are_retried(self) -> None:
        attempts = []

        @self.fake.route("GET", "/open-apis/im/v1/chats/oc_2")
        def chat(request):
            attempts.append(1)
            if len(attempts) == 1:
                return 400, {"code": 99991400, "msg": "too many requests"}, {"x-ogw-ratelimit-reset": "0.2"}
            return {"chat_id": "oc_2", "name": "限流"}

        self.assertEqual(self.run_async(self.lark.chats.get("oc_2")).name, "限流")
        self.assertEqual(len(attempts), 2)

    def test_scope_errors_say_what_is_missing(self) -> None:
        @self.fake.route("POST", "/open-apis/im/v1/messages")
        def send(request):
            return 400, {"code": 99991679, "msg": "Unauthorized",
                         "error": {"permission_violations": [{"type": "action_scope_required",
                                                              "subject": "im:message.send_as_user"}]}}

        @self.fake.route("GET", "/open-apis/im/v1/chats/oc_3")
        def chat(request):
            return 400, {"code": 99991672, "msg": "Access denied",
                         "error": {"permission_violations": [{"subject": "im:chat:read"}]}}

        with self.assertRaises(self.lark.LarkScopeError) as user_side:
            self.run_async(self.lark.messages.send("oc_3", "hi"))
        self.assertEqual(user_side.exception.scopes, ["im:message.send_as_user"])
        self.assertFalse(user_side.exception.app_side)
        self.assertIn("login(scopes=", str(user_side.exception))

        with self.assertRaises(self.lark.LarkScopeError) as app_side:
            self.run_async(self.lark.chats.get("oc_3"))
        self.assertTrue(app_side.exception.app_side)
        self.assertIn("/page/launcher?clientID=cli_test&addons=", app_side.exception.console_url)

    def test_bot_identity_uses_the_tenant_token(self) -> None:
        @self.fake.route("GET", "/open-apis/im/v1/chats")
        def chats(request):
            return {"items": [{"chat_id": "oc_bot", "name": "机器人群"}], "has_more": False}

        async def main():
            return [chat async for chat in self.lark.bot.chats.list()]

        chats = self.run_async(main())
        self.assertEqual(chats[0].lark.identity, "bot")
        self.assertEqual(self.fake.calls("GET", "/open-apis/im/v1/chats")[0].token, "t-at")

    def test_service_attributes_stay_services(self) -> None:
        import importlib

        # Importing every submodule must not shadow the services of the same name.
        for module in ("_im", "_contact", "_docs", "_wiki", "_drive", "_sheets", "_bitable", "_cloud", "_raw", "_auth"):
            importlib.import_module(f"locus_lark.{module}")
        for name in ("chats", "messages", "users", "docs", "wiki", "drive", "sheets", "base", "api", "auth"):
            self.assertIsInstance(getattr(self.lark, name), self.lark._core.Service, name)

    def test_raw_api_and_binary_replies(self) -> None:
        @self.fake.route("GET", "/open-apis/calendar/v4/calendars")
        def calendars(request):
            return {"calendar_list": [{"calendar_id": "c1"}], "page_size": request.arg("page_size")}

        @self.fake.route("GET", "/open-apis/drive/v1/medias/box1/download")
        def download(request):
            return b"\x89PNG..."

        async def main():
            data = await self.lark.api.get("/open-apis/calendar/v4/calendars", {"page_size": 5})
            blob = await self.lark.api.get("/open-apis/drive/v1/medias/box1/download")
            return data, blob

        data, blob = self.run_async(main())
        self.assertEqual(data["calendar_list"][0]["calendar_id"], "c1")
        self.assertEqual(data["page_size"], "5")
        self.assertEqual(blob, b"\x89PNG...")

    # ── chats and messages ──────────────────────────────────────────────

    def test_find_chat_and_page_through_history(self) -> None:
        @self.fake.route("GET", "/open-apis/im/v1/chats/search")
        def search(request):
            return {"items": [{"chat_id": "oc_a", "name": "项目群 2"}, {"chat_id": "oc_b", "name": "项目群"}],
                    "has_more": False}

        pages = {
            None: {"items": [_message("om_1", "大家好 @_user_1", mentions=[{"key": "@_user_1", "name": "张三"}])],
                   "has_more": True, "page_token": "p2"},
            "p2": {"items": [_message("om_2", "收到", sender="ou_2")], "has_more": False},
        }

        @self.fake.route("GET", "/open-apis/im/v1/messages")
        def messages(request):
            return pages[request.arg("page_token")]

        async def main():
            chat = await self.lark.chats.find("项目群")
            items = [message async for message in chat.messages(since="2026-11-01T00:00:00+08:00")]
            return chat, items

        chat, items = self.run_async(main())
        self.assertEqual(chat.id, "oc_b")                      # the exact name wins
        self.assertEqual([m.id for m in items], ["om_1", "om_2"])
        self.assertEqual(items[0].text, "大家好 @张三")
        self.assertEqual(items[1].sender.id, "ou_2")
        first = self.fake.calls("GET", "/open-apis/im/v1/messages")[0]
        self.assertEqual(first.arg("container_id"), "oc_b")
        self.assertEqual(first.arg("start_time"), "1793462400")   # 2026-11-01 00:00 +08:00
        self.assertEqual(first.arg("sort_type"), "ByCreateTimeAsc")

    def test_send_markdown_image_and_reply(self) -> None:
        @self.fake.route("POST", "/open-apis/im/v1/messages")
        def send(request):
            body = request.json
            return {"message_id": "om_new", "chat_id": body["receive_id"], "msg_type": body["msg_type"],
                    "body": {"content": body["content"]}}

        @self.fake.route("POST", "/open-apis/im/v1/images")
        def image(request):
            assert b"image_type" in request.body and b"PNGDATA" in request.body
            return {"image_key": "img_v2_1"}

        @self.fake.route("POST", "/open-apis/im/v1/messages/:id/reply")
        def reply(request):
            return {"message_id": "om_reply", "msg_type": request.json["msg_type"],
                    "body": {"content": request.json["content"]}}

        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "chart.png"
            picture.write_bytes(b"PNGDATA")

            async def main():
                chat = self.lark.chats.ref("oc_x")
                sent = await chat.send(markdown="**粗体**")
                await chat.send(image=str(picture))
                await self.lark.messages.send("someone@corp.com", "hi")
                answer = await sent.reply("收到", in_thread=True)
                return sent, answer

            sent, answer = self.run_async(main())

        bodies = [request.json for request in self.fake.calls("POST", "/open-apis/im/v1/messages")]
        self.assertEqual(bodies[0]["msg_type"], "post")
        self.assertEqual(json.loads(bodies[0]["content"]), {"zh_cn": {"content": [[{"tag": "md", "text": "**粗体**"}]]}})
        self.assertEqual(json.loads(bodies[1]["content"]), {"image_key": "img_v2_1"})
        self.assertEqual(self.fake.calls("POST", "/open-apis/im/v1/messages")[2].arg("receive_id_type"), "email")
        self.assertTrue(all(body["uuid"] for body in bodies))
        self.assertEqual(sent.text, "**粗体**")
        self.assertEqual(self.body("POST", "/open-apis/im/v1/messages/om_new/reply")["reply_in_thread"], True)
        self.assertEqual(answer.text, "收到")

    def test_message_search_resolves_messages(self) -> None:
        @self.fake.route("POST", "/open-apis/im/v1/messages/search")
        def search(request):
            return {"items": [{"meta_data": {"message_id": "om_9"}}], "has_more": False}

        @self.fake.route("GET", "/open-apis/im/v1/messages/mget")
        def mget(request):
            return {"items": [_message(message_id, "命中") for message_id in request.query["message_ids"]]}

        async def main():
            return [m async for m in self.lark.messages.search("发布", chats=["oc_1"], at_me=True)]

        found = self.run_async(main())
        self.assertEqual([m.text for m in found], ["命中"])
        body = self.body("POST", "/open-apis/im/v1/messages/search")
        self.assertEqual(body["query"], "发布")
        self.assertEqual(body["filter"]["chat_ids"], ["oc_1"])
        self.assertTrue(body["filter"]["is_at_me"])

    # ── documents and wiki ──────────────────────────────────────────────

    def test_open_wiki_link_and_edit_the_document(self) -> None:
        @self.fake.route("GET", "/open-apis/wiki/v2/spaces/get_node")
        def node(request):
            assert request.arg("token") == "wikTok1"
            return {"node": {"node_token": "wikTok1", "space_id": "sp1", "obj_token": "doxTok1", "obj_type": "docx",
                             "title": "设计"}}

        @self.fake.route("POST", "/open-apis/docs_ai/v1/documents/doxTok1/fetch")
        def fetch(request):
            return {"document": {"title": "设计", "content": f"# 设计 ({request.json['format']})"}}

        @self.fake.route("PUT", "/open-apis/docs_ai/v1/documents/doxTok1")
        def update(request):
            return {"document": {"revision_id": 7}}

        async def main():
            doc = await self.lark.open("https://corp.feishu.cn/wiki/wikTok1?from=x")
            text = await doc.markdown()
            xml = await doc.xml()
            await doc.append("## 结论")
            await doc.replace("旧", "新")
            return doc, text, xml

        doc, text, xml = self.run_async(main())
        self.assertEqual(type(doc).__name__, "Document")
        self.assertEqual(text, "# 设计 (markdown)")
        self.assertEqual(xml, "# 设计 (xml)")
        fetches = [request.json for request in self.fake.calls("POST", "/open-apis/docs_ai/v1/documents/doxTok1/fetch")]
        self.assertFalse(fetches[0]["export_option"]["export_block_id"])
        self.assertTrue(fetches[1]["export_option"]["export_block_id"])
        append, replace = [request.json for request in self.fake.calls("PUT", "/open-apis/docs_ai/v1/documents/doxTok1")]
        self.assertEqual((append["command"], append["block_id"], append["content"]), ("block_insert_after", "-1", "## 结论"))
        self.assertEqual((replace["command"], replace["pattern"], replace["content"]), ("str_replace", "旧", "新"))

    def test_walk_a_wiki_space(self) -> None:
        tree = {None: [("n1", True), ("n2", False)], "n1": [("n1a", False)]}

        @self.fake.route("GET", "/open-apis/wiki/v2/spaces/sp1/nodes")
        def nodes(request):
            parent = request.arg("parent_node_token")
            return {"items": [{"node_token": token, "title": token, "space_id": "sp1", "obj_type": "docx",
                               "has_child": has_child, "parent_node_token": parent}
                              for token, has_child in tree[parent]], "has_more": False}

        async def main():
            space = self.lark.wiki.space_ref("sp1")
            return [node.id async for node in space.walk()]

        self.assertEqual(self.run_async(main()), ["n1", "n1a", "n2"])

    # ── sheets and bases ────────────────────────────────────────────────

    def test_worksheet_read_and_write_ranges(self) -> None:
        @self.fake.route("GET", "/open-apis/sheets/v3/spreadsheets/shtTok/sheets/query")
        def query(request):
            return {"sheets": [{"sheet_id": "s1", "title": "明细", "index": 0,
                                "grid_properties": {"row_count": 20, "column_count": 3}}]}

        @self.fake.route("GET", "/open-apis/sheets/v2/spreadsheets/shtTok/values/:range")
        def read(request):
            return {"valueRange": {"values": [["名称", "数量"], ["苹果", 3], [None, None]]}}

        @self.fake.route("PUT", "/open-apis/sheets/v2/spreadsheets/shtTok/values")
        def write(request):
            return {"updatedRange": request.json["valueRange"]["range"]}

        async def main():
            book = await self.lark.open("https://corp.feishu.cn/sheets/shtTok?sheet=s1")
            sheet = await book.worksheet()
            records = await sheet.records()
            await sheet.write("B2", [[1, 2, 3], [4, 5, 6]])
            await sheet.clear("A5:B6")
            return sheet, records

        sheet, records = self.run_async(main())
        self.assertEqual(sheet.title, "明细")
        self.assertEqual(records, [{"名称": "苹果", "数量": 3}])
        self.assertEqual(self.fake.calls("GET", "/open-apis/sheets/v2/spreadsheets/shtTok/values/s1!A1:C20")[0].arg(
            "valueRenderOption"), "UnformattedValue")
        writes = [request.json["valueRange"] for request in self.fake.calls("PUT", "/open-apis/sheets/v2/spreadsheets/shtTok/values")]
        self.assertEqual(writes[0]["range"], "s1!B2:D3")
        self.assertEqual(writes[1], {"range": "s1!A5:B6", "values": [["", ""], ["", ""]]})

    def test_base_records_query_and_save(self) -> None:
        @self.fake.route("GET", "/open-apis/bitable/v1/apps/bascTok/tables")
        def tables(request):
            return {"items": [{"table_id": "tbl1", "name": "任务"}], "has_more": False}

        @self.fake.route("POST", "/open-apis/bitable/v1/apps/bascTok/tables/tbl1/records/search")
        def search(request):
            return {"items": [{"record_id": "rec1", "fields": {"标题": "A", "分数": 90, "状态": "待办"}}], "has_more": False}

        @self.fake.route("PUT", "/open-apis/bitable/v1/apps/bascTok/tables/tbl1/records/rec1")
        def save(request):
            return {"record": {"record_id": "rec1", "fields": {"标题": "A", "分数": 90, **request.json["fields"]}}}

        async def main():
            base = await self.lark.open("https://corp.feishu.cn/base/bascTok?table=tbl1&view=vew1")
            table = await base.table()
            records = [r async for r in table.records(where={"状态": "待办", "分数": (">=", 80)}, sort=["-分数"])]
            record = records[0]
            record["状态"] = "完成"
            await record.save()
            return record

        record = self.run_async(main())
        self.assertEqual(record["状态"], "完成")
        body = self.body("POST", "/open-apis/bitable/v1/apps/bascTok/tables/tbl1/records/search")
        self.assertEqual(body["view_id"], "vew1")
        self.assertEqual(body["sort"], [{"field_name": "分数", "desc": True}])
        self.assertEqual(body["filter"], {"conjunction": "and", "conditions": [
            {"field_name": "状态", "operator": "is", "value": ["待办"]},
            {"field_name": "分数", "operator": "isGreaterEqual", "value": ["80"]}]})
        self.assertEqual(self.body("PUT", "/open-apis/bitable/v1/apps/bascTok/tables/tbl1/records/rec1"),
                         {"fields": {"状态": "完成"}})
        self.assertFalse(record.dirty)


def _message(message_id: str, text: str, *, sender: str = "ou_1", mentions=None) -> dict:
    return {"message_id": message_id, "chat_id": "oc_b", "msg_type": "text", "create_time": "1761926400000",
            "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"},
            "body": {"content": json.dumps({"text": text}, ensure_ascii=False)}, "mentions": mentions or []}


if __name__ == "__main__":
    unittest.main()
