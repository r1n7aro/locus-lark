"""The less common operations: chat management, reactions, pins, urgent,
announcements, comments, sharing, import and export, images in documents,
history, wiki changes, sheet structure and batch record edits."""

from __future__ import annotations

import base64
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from fake_lark import SignedInCase


class ChatEdgesTest(SignedInCase, unittest.TestCase):
    def test_chat_management(self) -> None:
        self.ok("POST", "/open-apis/im/v1/chats", lambda r: {"chat_id": "oc_new", "name": r.json["name"]})
        self.ok("POST", "/open-apis/im/v1/chats/oc_new/members", {"invalid_id_list": []})
        self.ok("DELETE", "/open-apis/im/v1/chats/oc_new/members")
        self.ok("POST", "/open-apis/im/v1/chats/oc_new/managers/add_managers")
        self.ok("PUT", "/open-apis/im/v1/chats/oc_new")
        self.ok("POST", "/open-apis/im/v1/chats/oc_new/link", {"share_link": "https://applink/x"})
        self.ok("GET", "/open-apis/im/v1/chats/oc_new/members",
                {"items": [{"member_id": "ou_a", "name": "甲"}], "has_more": False})
        self.ok("DELETE", "/open-apis/im/v1/chats/oc_new")

        async def main():
            user = self.lark.users.ref("ou_a", "甲")
            chat = await self.lark.chats.create("新群", users=[user, "ou_b"], bots=["cli_bot"])
            await chat.add_members(["ou_c"], bots=["cli_x"])
            await chat.remove_members(["ou_c"])
            await chat.add_managers([user])
            await chat.update(name="改名", owner=user, edit_permission="only_owner")
            link = await chat.share_link("year")
            members = [m async for m in chat.members()]
            await chat.disband()
            return chat, link, members

        chat, link, members = self.run_async(main())
        create = self.body("POST", "/open-apis/im/v1/chats")
        self.assertEqual(create["user_id_list"], ["ou_a", "ou_b"])
        self.assertEqual(create["bot_id_list"], ["cli_bot"])
        adds = self.fake.calls("POST", "/open-apis/im/v1/chats/oc_new/members")
        self.assertEqual([(r.arg("member_id_type"), r.json["id_list"]) for r in adds],
                         [("open_id", ["ou_c"]), ("app_id", ["cli_x"])])
        self.assertEqual(self.body("PUT", "/open-apis/im/v1/chats/oc_new"),
                         {"name": "改名", "owner_id": "ou_a", "edit_permission": "only_owner"})
        self.assertEqual(chat.name, "改名")
        self.assertEqual(link, "https://applink/x")
        self.assertEqual([(m.id, m.name) for m in members], [("ou_a", "甲")])
        self.assertEqual(len(self.fake.calls("DELETE", "/open-apis/im/v1/chats/oc_new")), 1)

    def test_reactions_pins_top_notice_forward(self) -> None:
        self.ok("POST", "/open-apis/im/v1/messages/om_1/reactions",
                {"reaction_id": "r1", "reaction_type": {"emoji_type": "OK"}})
        self.ok("GET", "/open-apis/im/v1/messages/om_1/reactions",
                {"items": [{"reaction_id": "r1", "reaction_type": {"emoji_type": "OK"},
                            "operator": {"operator_id": "ou_me"}}], "has_more": False})
        self.ok("DELETE", "/open-apis/im/v1/messages/om_1/reactions/r1")
        self.ok("POST", "/open-apis/im/v1/pins", lambda r: {"pin": {"message_id": r.json["message_id"], "chat_id": "oc_1"}})
        self.ok("DELETE", "/open-apis/im/v1/pins/om_1")
        self.ok("POST", "/open-apis/im/v1/chats/oc_1/top_notice/put_top_notice")
        self.ok("POST", "/open-apis/im/v1/chats/oc_1/top_notice/delete_top_notice")
        self.ok("POST", "/open-apis/im/v1/messages/om_1/forward", {"message_id": "om_fw"})
        self.ok("DELETE", "/open-apis/im/v1/messages/om_1")

        async def main():
            message = self.lark.messages.ref("om_1")
            reaction = await message.react("OK")
            listed = [r async for r in message.reactions()]
            await listed[0].remove()
            pin = await message.pin()
            await message.unpin()
            chat = self.lark.chats.ref("oc_1")
            await chat.set_top_notice(message)
            await chat.set_top_notice()
            await chat.clear_top_notice()
            forwarded = await message.forward("oc_2")
            await message.recall()
            return reaction, listed, pin, forwarded

        reaction, listed, pin, forwarded = self.run_async(main())
        self.assertEqual(reaction.emoji, "OK")
        self.assertEqual(listed[0].operator_id, "ou_me")
        self.assertEqual(pin.chat_id, "oc_1")
        notices = [r.json["chat_top_notice"] for r in
                   self.fake.calls("POST", "/open-apis/im/v1/chats/oc_1/top_notice/put_top_notice")]
        self.assertEqual(notices, [[{"action_type": "1", "message_id": "om_1"}], [{"action_type": "2"}]])
        self.assertEqual(self.fake.calls("POST", "/open-apis/im/v1/messages/om_1/forward")[0].arg("receive_id_type"), "chat_id")
        self.assertEqual(forwarded.id, "om_fw")

    def test_bot_only_calls_use_the_bot(self) -> None:
        self.ok("PATCH", "/open-apis/im/v1/messages/om_b/urgent_app", {"invalid_user_id_list": ["ou_x"]})
        self.ok("PUT", "/open-apis/im/v1/messages/om_b", {"message_id": "om_b"})
        self.ok("POST", "/open-apis/im/v1/messages/merge_forward", {"message": {"message_id": "om_merged"}})

        async def main():
            message = self.lark.messages.ref("om_b")
            failed = await message.urgent(["ou_a", "ou_x"])
            await message.edit("改过")
            merged = await self.lark.messages.merge_forward([message, "om_c"], "oc_9")
            return failed, merged

        failed, merged = self.run_async(main())
        self.assertEqual(failed, ["ou_x"])
        self.assertEqual(merged.id, "om_merged")
        for method, path in (("PATCH", "/open-apis/im/v1/messages/om_b/urgent_app"),
                             ("PUT", "/open-apis/im/v1/messages/om_b"),
                             ("POST", "/open-apis/im/v1/messages/merge_forward")):
            self.assertEqual(self.fake.calls(method, path)[0].token, "t-at", path)
        self.assertEqual(self.body("POST", "/open-apis/im/v1/messages/merge_forward")["message_id_list"], ["om_b", "om_c"])

    def test_announcement(self) -> None:
        base = "/open-apis/docx/v1/chats/oc_1/announcement"
        blocks = [{"block_id": "oc_1", "block_type": 1, "children": ["b1", "b2"]},
                  {"block_id": "b1", "block_type": 2, "parent_id": "oc_1",
                   "text": {"elements": [{"text_run": {"content": "第一段"}}]}},
                  {"block_id": "b2", "block_type": 2, "parent_id": "oc_1",
                   "text": {"elements": [{"text_run": {"content": "第二段"}}]}}]
        self.ok("GET", base + "/blocks", {"items": blocks, "has_more": False})
        self.ok("POST", base + "/blocks/oc_1/children")
        self.ok("DELETE", base + "/blocks/oc_1/children/batch_delete")

        async def main():
            announcement = self.lark.chats.ref("oc_1").announcement
            text = await announcement.text()
            await announcement.set("新公告\n第二行")
            return text

        self.assertEqual(self.run_async(main()), "第一段\n第二段")
        self.assertEqual(self.body("DELETE", base + "/blocks/oc_1/children/batch_delete"), {"start_index": 0, "end_index": 2})
        children = self.body("POST", base + "/blocks/oc_1/children")["children"]
        self.assertEqual([c["text"]["elements"][0]["text_run"]["content"] for c in children], ["新公告", "第二行"])

    def test_download_message_file(self) -> None:
        self.ok("GET", "/open-apis/im/v1/messages/om_f/resources/file_1", b"FILEDATA")
        message = self.lark.Message(self.lark.user, {
            "message_id": "om_f", "msg_type": "file",
            "body": {"content": json.dumps({"file_key": "file_1", "file_name": "报告.pdf"})}})
        with tempfile.TemporaryDirectory() as folder:
            path = self.run_async(message.download(folder))
            self.assertEqual(path.name, "报告.pdf")
            self.assertEqual(path.read_bytes(), b"FILEDATA")
        self.assertEqual(self.fake.calls("GET", "/open-apis/im/v1/messages/om_f/resources/file_1")[0].arg("type"), "file")


class CloudEdgesTest(SignedInCase, unittest.TestCase):
    def test_comments_and_collaborators(self) -> None:
        files = "/open-apis/drive/v1/files/doxD/comments"
        perms = "/open-apis/drive/v1/permissions/doxD/members"
        self.ok("GET", files, {"items": [{"comment_id": "c1", "is_solved": False, "quote": "原文",
                                          "reply_list": {"replies": [{"reply_id": "r1", "content": {"elements": [
                                              {"type": "text_run", "text_run": {"text": "请改"}}]}}]}}],
                               "has_more": False})
        self.ok("POST", files + "/c1/replies", {"reply_id": "r2", "content": {"elements": [
            {"type": "text_run", "text_run": {"text": "已改"}}]}})
        self.ok("PATCH", files + "/c1")
        self.ok("POST", files, {"comment_id": "c2"})

        def add_member(request):
            if request.json["member_id"] == "ou_exists":
                return 400, {"code": 1063006, "msg": "member already exist"}
            return {"member": request.json}

        self.fake.route("POST", perms)(add_member)
        self.ok("PUT", perms + "/ou_exists", lambda r: {"member": r.json})
        self.ok("GET", perms, {"items": [{"member_type": "openid", "member_id": "ou_a", "perm": "edit", "name": "甲"}]})
        self.ok("DELETE", perms + "/ou_a")
        self.ok("PATCH", "/open-apis/drive/v1/permissions/doxD/public", lambda r: {"permission_public": r.json})

        async def main():
            doc = self.lark.docs.ref("doxD")
            comments = [c async for c in doc.comments(solved=False)]
            reply = await comments[0].reply("已改")
            await comments[0].resolve()
            whole = await doc.comment("整体意见")
            shared = await doc.share("ou_new", "edit", notify=True)
            updated = await doc.share("ou_exists", "view")
            members = await doc.members()
            await members[0].remove()
            sharing = await doc.set_sharing(link="tenant_readable")
            return comments, reply, whole, shared, updated, sharing

        comments, reply, whole, shared, updated, sharing = self.run_async(main())
        self.assertEqual((comments[0].text, comments[0].quote), ("请改", "原文"))
        self.assertEqual(reply.text, "已改")
        self.assertTrue(comments[0].solved)
        self.assertEqual(self.fake.calls("GET", files)[0].arg("file_type"), "docx")
        self.assertEqual(self.fake.calls("GET", files)[0].arg("is_solved"), "false")
        self.assertEqual(whole.id, "c2")
        self.assertEqual((shared.kind, shared.perm), ("openid", "edit"))
        self.assertEqual(self.fake.calls("POST", perms)[0].arg("need_notification"), "true")
        self.assertEqual(updated.perm, "view")                  # fell back to updating
        self.assertEqual(self.fake.calls("DELETE", perms + "/ou_a")[0].arg("member_type"), "openid")
        self.assertEqual(sharing, {"link_share_entity": "tenant_readable"})

    def test_import_and_export(self) -> None:
        self.ok("POST", "/open-apis/drive/v1/medias/upload_all", {"file_token": "boxImp"})
        self.ok("POST", "/open-apis/drive/v1/import_tasks", {"ticket": "t1"})
        polls = []

        def import_status(request):
            polls.append(1)
            if len(polls) == 1:
                return {"result": {"job_status": 2}}
            return {"result": {"job_status": 0, "token": "doxNew", "type": "docx", "url": "https://x/docx/doxNew"}}

        self.fake.route("GET", "/open-apis/drive/v1/import_tasks/t1")(import_status)
        self.ok("POST", "/open-apis/drive/v1/export_tasks", {"ticket": "e1"})
        self.ok("GET", "/open-apis/drive/v1/export_tasks/e1",
                {"result": {"job_status": 0, "file_token": "boxExp", "file_name": "报表"}})
        self.ok("GET", "/open-apis/drive/v1/export_tasks/file/boxExp/download", b"XLSXDATA")

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "notes.md"
            source.write_text("# 笔记", encoding="utf-8")

            async def main():
                doc = await self.lark.drive.import_file(source)
                path = await self.lark.sheets.ref("shtE").export(folder, "xlsx")
                return doc, path

            doc, path = self.run_async(main())
            self.assertEqual(path.name, "报表.xlsx")
            self.assertEqual(path.read_bytes(), b"XLSXDATA")
        self.assertEqual((type(doc).__name__, doc.id), ("Document", "doxNew"))
        upload = self.fake.calls("POST", "/open-apis/drive/v1/medias/upload_all")[0].body
        self.assertIn(b'name="parent_type"\r\n\r\nccm_import_open', upload)
        self.assertIn(b'{"obj_type": "docx", "file_extension": "md"}', upload)
        task = self.body("POST", "/open-apis/drive/v1/import_tasks")
        self.assertEqual((task["type"], task["file_extension"], task["file_name"]), ("docx", "md", "notes"))
        self.assertEqual(self.body("POST", "/open-apis/drive/v1/export_tasks"),
                         {"file_extension": "xlsx", "token": "shtE", "type": "sheet"})

    def test_drive_listing_upload_copy_move(self) -> None:
        self.ok("GET", "/open-apis/drive/explorer/v2/root_folder/meta", {"token": "fldRoot"})
        self.ok("GET", "/open-apis/drive/v1/files", {"files": [], "items": [
            {"token": "doxA", "type": "docx", "name": "文档"}, {"token": "fldB", "type": "folder", "name": "目录"}],
            "has_more": False})
        self.ok("POST", "/open-apis/drive/v1/files/upload_all", {"file_token": "boxUp"})
        self.ok("POST", "/open-apis/drive/v1/files/doxA/copy", {"file": {"token": "doxCopy", "type": "docx", "name": "副本"}})
        self.ok("POST", "/open-apis/drive/v1/files/doxA/move")

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "a.txt"
            source.write_bytes(b"hello")

            async def main():
                root = await self.lark.drive.root()
                items = [item async for item in root.files()]
                uploaded = await root.upload(source)
                doc = items[0]
                copy = await doc.copy("副本", folder=items[1])
                await doc.move(items[1])
                return items, uploaded, copy

            items, uploaded, copy = self.run_async(main())
        self.assertEqual([(type(i).__name__, i.name, i.doc_type) for i in items],
                         [("Document", "文档", "docx"), ("Folder", "目录", "folder")])
        self.assertEqual(uploaded.token, "boxUp")
        self.assertIn(b"fldRoot", self.fake.calls("POST", "/open-apis/drive/v1/files/upload_all")[0].body)
        self.assertEqual((type(copy).__name__, copy.id), ("Document", "doxCopy"))
        self.assertEqual(self.body("POST", "/open-apis/drive/v1/files/doxA/move"), {"type": "docx", "folder_token": "fldB"})


class DocumentEdgesTest(SignedInCase, unittest.TestCase):
    def test_insert_image_after_a_block(self) -> None:
        root = "/open-apis/docx/v1/documents/doxI/blocks/doxI"
        self.ok("GET", "/open-apis/docx/v1/documents/doxI/blocks", {"items": [
            {"block_id": "doxI", "block_type": 1, "children": ["h1", "p1"]},
            {"block_id": "h1", "block_type": 3, "parent_id": "doxI", "heading1": {"elements": [{"text_run": {"content": "标题"}}]}},
            {"block_id": "p1", "block_type": 2, "parent_id": "doxI", "text": {"elements": [{"text_run": {"content": "正文"}}]}},
        ], "has_more": False})
        self.ok("POST", root + "/children", {"children": [{"block_id": "img1", "block_type": 27}]})
        self.ok("POST", "/open-apis/drive/v1/medias/upload_all", {"file_token": "boxImg"})
        self.ok("PATCH", "/open-apis/docx/v1/documents/doxI/blocks/batch_update")

        with tempfile.TemporaryDirectory() as folder:
            picture = Path(folder) / "chart.png"
            picture.write_bytes(b"PNG")

            async def main():
                doc = self.lark.docs.ref("doxI")
                top = await doc.blocks()
                block = await doc.insert_image(picture, after=top[0], width=640, align="center", caption="图 1")
                return top, block

            top, block = self.run_async(main())
        self.assertEqual([(b.type, b.text) for b in top], [("heading1", "标题"), ("text", "正文")])
        self.assertEqual(block.id, "img1")
        self.assertEqual(self.body("POST", root + "/children"), {"children": [{"block_type": 27, "image": {}}], "index": 1})
        upload = self.fake.calls("POST", "/open-apis/drive/v1/medias/upload_all")[0].body
        self.assertIn(b"docx_image", upload)
        self.assertIn(b'{"drive_route_token":"doxI"}', upload)
        request = self.body("PATCH", "/open-apis/docx/v1/documents/doxI/blocks/batch_update")["requests"][0]
        self.assertEqual(request, {"block_id": "img1", "replace_image": {"token": "boxImg", "width": 640, "align": 2,
                                                                         "caption": {"content": "图 1"}}})

    def test_history_and_revert(self) -> None:
        base = "/open-apis/docs_ai/v1/documents/doxH"
        self.ok("GET", base + "/histories", {"entries": [
            {"history_version_id": "12", "revision_id": 42, "edit_time": "2026-06-22T12:25:14Z", "editor_ids": ["ou_a"]},
            {"history_version_id": "11", "revision_id": 41, "edit_time": "2026-06-22T12:24:45Z"}], "has_more": False})
        self.ok("POST", base + "/history/revert", {"task_id": "task1", "status": "running", "poll_after_ms": 10})
        self.ok("GET", base + "/history/revert_status", {"status": "done", "history_version_id": "11"})

        async def main():
            doc = self.lark.docs.ref("doxH")
            versions = await doc.history()
            result = await doc.revert(versions[-1])
            return versions, result

        versions, result = self.run_async(main())
        self.assertEqual([(v.id, v.revision_id, v.editor_ids) for v in versions], [("12", 42, ["ou_a"]), ("11", 41, [])])
        self.assertEqual(versions[0].created_at.year, 2026)
        self.assertEqual(self.body("POST", base + "/history/revert")["history_version_id"], "11")
        self.assertEqual(result["status"], "done")

    def test_create_document_with_markdown(self) -> None:
        self.ok("POST", "/open-apis/docx/v1/documents", {"document": {"document_id": "doxC", "title": "周报"}})
        self.ok("PUT", "/open-apis/docs_ai/v1/documents/doxC")
        self.ok("POST", "/open-apis/search/v2/doc_wiki/search", {"res_units": [
            {"title_highlighted": "<h>周报</h>", "result_meta": {"url": "https://x/docx/doxC", "doc_types": "DOCX"}}],
            "has_more": False})

        async def main():
            doc = await self.lark.docs.create("周报", "# 本周", folder="fldX")
            results = [r async for r in self.lark.docs.search("周报", kinds=["docx"])]
            return doc, results

        doc, results = self.run_async(main())
        self.assertEqual(doc.title, "周报")
        self.assertEqual(self.body("POST", "/open-apis/docx/v1/documents"), {"title": "周报", "folder_token": "fldX"})
        self.assertEqual(self.body("PUT", "/open-apis/docs_ai/v1/documents/doxC")["content"], "# 本周")
        self.assertEqual(results[0].title, "周报")
        self.assertEqual(self.body("POST", "/open-apis/search/v2/doc_wiki/search")["doc_filter"], {"doc_types": ["DOCX"]})


class WikiSheetBaseEdgesTest(SignedInCase, unittest.TestCase):
    def test_wiki_changes(self) -> None:
        node = {"node_token": "wikN", "space_id": "sp1", "obj_token": "doxN", "obj_type": "docx", "title": "页"}
        self.ok("POST", "/open-apis/wiki/v2/spaces/sp1/nodes",
                lambda r: {"node": {**node, "node_token": "wikNew", "title": r.json.get("title")}})
        self.ok("PUT", "/open-apis/docs_ai/v1/documents/doxN")
        self.ok("POST", "/open-apis/wiki/v2/spaces/sp1/nodes/wikN/update_title")
        self.ok("POST", "/open-apis/wiki/v2/spaces/sp1/nodes/wikN/move", {"node": {"node_token": "wikN", "parent_node_token": "wikP"}})
        self.ok("POST", "/open-apis/wiki/v2/spaces/sp1/nodes/wikN/copy", {"node": {**node, "node_token": "wikCopy"}})
        self.ok("POST", "/open-apis/wiki/v2/spaces/sp1/members", lambda r: {"member": r.json})

        async def main():
            space = self.lark.wiki.space_ref("sp1")
            created = await space.create_node("新页", markdown="内容")
            page = self.lark.wiki.node_ref(node)
            await page.rename("新标题")
            await page.move(parent="wikP")
            copy = await page.copy(title="副本")
            member = await space.add_member("someone@corp.com", "admin")
            return created, page, copy, member

        created, page, copy, member = self.run_async(main())
        self.assertEqual(created.title, "新页")
        self.assertEqual(page.title, "新标题")
        self.assertEqual(page.parent_token, "wikP")
        self.assertEqual(copy.id, "wikCopy")
        self.assertEqual(member, {"member_type": "email", "member_id": "someone@corp.com", "member_role": "admin"})
        self.assertEqual(self.body("POST", "/open-apis/wiki/v2/spaces/sp1/nodes/wikN/move"),
                         {"target_parent_token": "wikP", "target_space_id": "sp1"})

    def test_sheet_structure(self) -> None:
        v2 = "/open-apis/sheets/v2/spreadsheets/shtS"
        self.ok("POST", "/open-apis/sheets/v3/spreadsheets/shtS/sheets/s1/find",
                {"find_result": {"matched_cells": ["A2", "A5"]}})
        self.ok("POST", v2 + "/insert_dimension_range")
        self.ok("DELETE", v2 + "/dimension_range")
        self.ok("POST", v2 + "/merge_cells")
        self.ok("POST", v2 + "/values_append")

        async def main():
            book = self.lark.sheets.ref("shtS")
            sheet = self.lark.Worksheet(self.lark.user, {"sheet_id": "s1", "title": "表",
                                                         "grid_properties": {"row_count": 10, "column_count": 4}}, book)
            cells = await sheet.find("苹果", cells="A:A", whole_cell=True)
            await sheet.insert_rows(5, count=2)
            await sheet.delete_rows(10, count=3)
            await sheet.merge("A1:C1")
            await sheet.append([["x", "y"]])
            return cells

        self.assertEqual(self.run_async(main()), ["A2", "A5"])
        self.assertEqual(self.body("POST", "/open-apis/sheets/v3/spreadsheets/shtS/sheets/s1/find")["find_condition"],
                         {"range": "s1!A:A", "match_case": False, "match_entire_cell": True, "search_by_regex": False})
        self.assertEqual(self.body("POST", v2 + "/insert_dimension_range")["dimension"],
                         {"sheetId": "s1", "majorDimension": "ROWS", "startIndex": 4, "endIndex": 6})
        self.assertEqual(self.body("DELETE", v2 + "/dimension_range")["dimension"],
                         {"sheetId": "s1", "majorDimension": "ROWS", "startIndex": 10, "endIndex": 12})
        self.assertEqual(self.body("POST", v2 + "/merge_cells"), {"range": "s1!A1:C1", "mergeType": "MERGE_ALL"})
        self.assertEqual(self.body("POST", v2 + "/values_append")["valueRange"]["range"], "s1!A1:D")

    def test_batch_records_and_fields(self) -> None:
        base = "/open-apis/bitable/v1/apps/bascB/tables/tblB"
        self.ok("POST", base + "/records/batch_create",
                lambda r: {"records": [{"record_id": f"rec{i}", "fields": item["fields"]} for i, item in enumerate(r.json["records"])]})
        self.ok("POST", base + "/records/batch_delete")
        self.ok("POST", base + "/records/batch_update", lambda r: {"records": r.json["records"]})
        self.ok("POST", base + "/fields", lambda r: {"field": {"field_id": "fld1", **r.json}})

        async def main():
            table = self.lark.Table(self.lark.user, {"table_id": "tblB", "name": "表"},
                                    self.lark.base.ref("bascB"))
            created = await table.add_many([{"n": i} for i in range(750)])
            created[0]["n"] = -1
            await table.update_many([created[0], ("rec9", {"n": 9})])
            await table.delete_many(created[:600])
            field = await table.add_field("状态", "single_select", options=[{"name": "完成"}])
            return created, field

        created, field = self.run_async(main())
        self.assertEqual(len(created), 750)
        self.assertEqual([len(r.json["records"]) for r in self.fake.calls("POST", base + "/records/batch_create")], [500, 250])
        self.assertEqual([len(r.json["records"]) for r in self.fake.calls("POST", base + "/records/batch_delete")], [500, 100])
        self.assertEqual(self.body("POST", base + "/records/batch_update")["records"],
                         [{"record_id": "rec0", "fields": {"n": -1}}, {"record_id": "rec9", "fields": {"n": 9}}])
        self.assertFalse(created[0].dirty)
        self.assertEqual((field.type, field.raw["property"]), (3, {"options": [{"name": "完成"}]}))


def _decode_addons(value: str) -> dict:
    return json.loads(gzip.decompress(base64.urlsafe_b64decode(value + "==")))


if __name__ == "__main__":
    unittest.main()
