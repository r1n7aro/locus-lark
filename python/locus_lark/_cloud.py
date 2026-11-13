"""What every cloud object shares: comments, collaborators, public sharing,
export, and its place in Drive.

``Document``, ``Spreadsheet``, ``Base`` and ``DriveFile`` mix this in;
``doc_type`` names the object for the permission and comment APIs (``docx``,
``sheet``, ``bitable``, ``file``, ``folder``).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from ._core import LarkError, Resource
from ._util import elements_text, from_timestamp, safe_name, text_elements, user_id_type, write_file

_PERMS = ("view", "edit", "full_access")
_EXPORT_FORMATS = {"docx": ("docx", "pdf", "md"), "sheet": ("xlsx", "csv"), "bitable": ("xlsx", "csv")}
_TASK_TIMEOUT = 300.0


def member_target(who: Any) -> tuple[str, str]:
    """``(member_type, member_id)`` for a User, Chat, open id, chat id, email or department id."""
    from ._contact import User
    from ._im import Chat

    if isinstance(who, User):
        return "openid", who.id
    if isinstance(who, Chat):
        return "openchat", who.id
    text = str(who).strip()
    if text.startswith("oc_"):
        return "openchat", text
    if text.startswith("od-"):
        return "opendepartmentid", text
    kind = user_id_type(text)
    return {"email": "email", "open_id": "openid", "union_id": "unionid"}.get(kind, "userid"), text


class CommentReply(Resource):
    _id_key = "reply_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], comment: "Comment") -> None:
        super().__init__(lark, raw)
        self.comment = comment

    @property
    def text(self) -> str:
        return elements_text((self.raw.get("content") or {}).get("elements"))

    @property
    def author_id(self) -> str | None:
        return self.raw.get("user_id")

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("create_time"))

    def _label(self) -> str:
        return f" {self.text[:40]!r}"

    def _path(self) -> str:
        return f"{self.comment._path()}/replies/{self.id}"

    async def edit(self, text: str) -> None:
        await self.lark.request("PUT", self._path(), params={"file_type": self.comment.owner.doc_type, "user_id_type": "open_id"},
                                json={"content": {"elements": text_elements(text)}})
        self.raw["content"] = {"elements": text_elements(text)}

    async def delete(self) -> None:
        await self.lark.request("DELETE", self._path(), params={"file_type": self.comment.owner.doc_type})


class Comment(Resource):
    """A comment thread: the first reply is the comment itself."""

    _id_key = "comment_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], owner: "CloudObject") -> None:
        super().__init__(lark, raw)
        self.owner = owner

    @property
    def replies(self) -> list[CommentReply]:
        return [CommentReply(self.lark, item, self) for item in (self.raw.get("reply_list") or {}).get("replies") or []]

    @property
    def text(self) -> str:
        replies = self.replies
        return replies[0].text if replies else ""

    @property
    def quote(self) -> str | None:
        """The commented passage (local comments)."""
        return self.raw.get("quote")

    @property
    def solved(self) -> bool:
        return bool(self.raw.get("is_solved"))

    @property
    def whole_document(self) -> bool:
        return bool(self.raw.get("is_whole"))

    @property
    def author_id(self) -> str | None:
        return self.raw.get("user_id")

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("create_time"))

    def _label(self) -> str:
        return f" {self.text[:40]!r}{' solved' if self.solved else ''}"

    def _path(self) -> str:
        return f"/open-apis/drive/v1/files/{self.owner.token}/comments/{self.id}"

    async def reply(self, text: str) -> CommentReply:
        data = await self.lark.request("POST", self._path() + "/replies",
                                       params={"file_type": self.owner.doc_type, "user_id_type": "open_id"},
                                       json={"content": {"elements": text_elements(text)}})
        reply = CommentReply(self.lark, data, self)
        self.raw.setdefault("reply_list", {}).setdefault("replies", []).append(reply.raw)
        return reply

    async def all_replies(self) -> AsyncIterator[CommentReply]:
        async for item in self.lark.pages("GET", self._path() + "/replies",
                                          params={"file_type": self.owner.doc_type, "user_id_type": "open_id"},
                                          page_size=100):
            yield CommentReply(self.lark, item, self)

    async def resolve(self, solved: bool = True) -> None:
        await self.lark.request("PATCH", self._path(), params={"file_type": self.owner.doc_type},
                                json={"is_solved": solved})
        self.raw["is_solved"] = solved

    async def reopen(self) -> None:
        await self.resolve(False)


class Member(Resource):
    """A collaborator of a cloud object and their permission (``view``,
    ``edit`` or ``full_access``)."""

    _id_key = "member_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], owner: "CloudObject") -> None:
        super().__init__(lark, raw)
        self.owner = owner

    @property
    def kind(self) -> str:
        return str(self.raw.get("member_type") or "")

    @property
    def perm(self) -> str:
        return str(self.raw.get("perm") or "")

    @property
    def name(self) -> str | None:
        return self.raw.get("name")

    def _label(self) -> str:
        return f" {self.name or self.kind} {self.perm}"

    async def set_perm(self, perm: str) -> None:
        await self.owner.share(self.id, perm, member_type=self.kind, update=True)
        self.raw["perm"] = perm

    async def remove(self) -> None:
        await self.lark.request("DELETE", f"/open-apis/drive/v1/permissions/{self.owner.token}/members/{self.id}",
                                params={"type": self.owner.doc_type, "member_type": self.kind})


class CloudObject(Resource):
    """Mixin of the cloud objects; needs ``token`` and ``doc_type``."""

    doc_type = "file"

    @property
    def token(self) -> str:
        return self.id

    @property
    def url(self) -> str | None:
        return self.raw.get("url")

    @property
    def name(self) -> str:
        """The title or file name (as far as loaded)."""
        return str(self.raw.get("name") or self.raw.get("title") or "")

    # ── comments ────────────────────────────────────────────────────────

    async def comments(self, *, solved: bool | None = None, whole: bool | None = None,
                       limit: int | None = None) -> AsyncIterator[Comment]:
        """Comment threads, optionally only (un)solved or whole-document ones."""
        params = {"file_type": self.doc_type, "is_solved": solved, "is_whole": whole, "user_id_type": "open_id"}
        async for item in self.lark.pages("GET", f"/open-apis/drive/v1/files/{self.token}/comments",
                                          params=params, page_size=100, limit=limit):
            yield Comment(self.lark, item, self)

    async def comment(self, text: str) -> Comment:
        """Adds a comment on the whole object."""
        data = await self.lark.request("POST", f"/open-apis/drive/v1/files/{self.token}/comments",
                                       params={"file_type": self.doc_type, "user_id_type": "open_id"},
                                       json={"reply_list": {"replies": [{"content": {"elements": text_elements(text)}}]}})
        return Comment(self.lark, data, self)

    # ── collaborators and sharing ───────────────────────────────────────

    async def members(self) -> list[Member]:
        data = await self.lark.request("GET", f"/open-apis/drive/v1/permissions/{self.token}/members",
                                       params={"type": self.doc_type, "fields": "*"})
        return [Member(self.lark, item, self) for item in data.get("items") or []]

    async def share(self, who: Any, perm: str = "view", *, notify: bool = False, member_type: str | None = None,
                    update: bool = False) -> Member:
        """Gives a person, chat, department or email ``perm`` (``view``,
        ``edit`` or ``full_access``); an existing collaborator is updated."""
        if perm not in _PERMS:
            raise ValueError(f"perm must be one of {', '.join(_PERMS)}")
        kind, member_id = (member_type, str(who)) if member_type else member_target(who)
        body = {"member_type": kind, "member_id": member_id, "perm": perm}
        path = f"/open-apis/drive/v1/permissions/{self.token}/members"
        params = {"type": self.doc_type, "need_notification": notify}
        if update:
            data = await self.lark.request("PUT", f"{path}/{member_id}", params=params, json=body)
        else:
            try:
                data = await self.lark.request("POST", path, params=params, json=body)
            except LarkError as error:
                # Already a collaborator: change the permission instead.
                if "exist" not in str(error).lower():
                    raise
                data = await self.lark.request("PUT", f"{path}/{member_id}", params=params, json=body)
        return Member(self.lark, data.get("member") or body, self)

    async def unshare(self, who: Any) -> None:
        kind, member_id = member_target(who)
        await self.lark.request("DELETE", f"/open-apis/drive/v1/permissions/{self.token}/members/{member_id}",
                                params={"type": self.doc_type, "member_type": kind})

    async def transfer_owner(self, who: Any, *, keep_access: bool = True) -> None:
        kind, member_id = member_target(who)
        await self.lark.request("POST", f"/open-apis/drive/v1/permissions/{self.token}/members/transfer_owner",
                                params={"type": self.doc_type, "remove_old_owner": not keep_access, "need_notification": True},
                                json={"member_type": kind, "member_id": member_id})

    async def sharing(self) -> dict[str, Any]:
        """Link sharing and security settings (``link_share_entity``,
        ``external_access``, ``comment_entity``, ``share_entity``, …)."""
        data = await self.lark.request("GET", f"/open-apis/drive/v1/permissions/{self.token}/public",
                                       params={"type": self.doc_type})
        return dict(data.get("permission_public") or data)

    async def set_sharing(self, *, link: str | None = None, external: bool | None = None, **settings: Any) -> dict[str, Any]:
        """Changes link sharing: ``link`` is ``closed``, ``tenant_readable``,
        ``tenant_editable``, ``anyone_readable`` or ``anyone_editable``;
        ``settings`` passes other fields of the public permission API."""
        body = {"link_share_entity": link, "external_access": external, **settings}
        data = await self.lark.request("PATCH", f"/open-apis/drive/v1/permissions/{self.token}/public",
                                       params={"type": self.doc_type}, json=body)
        return dict(data.get("permission_public") or data)

    # ── export, move, delete ────────────────────────────────────────────

    async def export(self, path: Any, format: str | None = None, *, sub_id: str | None = None) -> Path:
        """Exports to a local file (``docx``/``pdf``/``md`` for documents,
        ``xlsx``/``csv`` for sheets and bases; ``sub_id`` picks the sheet or
        table for ``csv``). ``path`` is a file or a folder."""
        formats = _EXPORT_FORMATS.get(self.doc_type)
        if not formats:
            raise LarkError(f"{self.doc_type} objects cannot be exported")
        target = Path(path).expanduser()
        format = format or (target.suffix.lstrip(".").lower() if target.suffix else formats[0])
        if format not in formats:
            raise ValueError(f"{self.doc_type} exports as {', '.join(formats)}")
        if format == "md" and self.doc_type == "docx":
            markdown = await self.markdown()  # type: ignore[attr-defined]
            if target.is_dir() or not target.suffix:
                target = target / f"{safe_name(await self._title())}.md"
            return write_file(target, markdown.encode("utf-8"))
        task = await self.lark.request("POST", "/open-apis/drive/v1/export_tasks",
                                       json={"file_extension": format, "token": self.token, "type": self.doc_type,
                                             "sub_id": sub_id})
        ticket = task["ticket"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TASK_TIMEOUT
        while True:
            result = (await self.lark.request("GET", f"/open-apis/drive/v1/export_tasks/{ticket}",
                                              params={"token": self.token})).get("result") or {}
            status = result.get("job_status")
            if status == 0 and result.get("file_token"):
                break
            if status not in (None, 1, 2):
                raise LarkError(f"Export failed: {result.get('job_error_msg') or status}", data=result)
            if loop.time() > deadline:
                raise LarkError("Export did not finish in time", data=result)
            await asyncio.sleep(1.5)
        data = await self.lark.request("GET", f"/open-apis/drive/v1/export_tasks/file/{result['file_token']}/download")
        if target.is_dir() or not target.suffix:
            target = target / f"{safe_name(result.get('file_name') or await self._title())}.{format}"
        return write_file(target, data)

    async def _title(self) -> str:
        return str(self.raw.get("title") or self.raw.get("name") or self.token)

    async def move(self, folder: Any) -> None:
        """Moves into a Drive folder (a Folder or folder token)."""
        folder_token = getattr(folder, "token", None) or str(folder)
        await self.lark.request("POST", f"/open-apis/drive/v1/files/{self.token}/move",
                                json={"type": self.doc_type, "folder_token": folder_token})

    async def copy(self, name: str, folder: Any = None) -> "CloudObject":
        folder_token = getattr(folder, "token", None) or (str(folder) if folder else "")
        data = await self.lark.request("POST", f"/open-apis/drive/v1/files/{self.token}/copy",
                                       params={"user_id_type": "open_id"},
                                       json={"name": name, "type": self.doc_type, "folder_token": folder_token})
        file = data.get("file") or {}
        from ._drive import open_typed

        return open_typed(self.lark, file.get("token"), file.get("type") or self.doc_type, file)

    async def delete(self) -> None:
        """Moves to the trash (Drive objects; wiki pages are removed from the wiki side)."""
        await self.lark.request("DELETE", f"/open-apis/drive/v1/files/{self.token}", params={"type": self.doc_type})

    async def meta(self) -> dict[str, Any]:
        """Title, owner, url and times."""
        data = await self.lark.request("POST", "/open-apis/drive/v1/metas/batch_query", params={"user_id_type": "open_id"},
                                       json={"request_docs": [{"doc_token": self.token, "doc_type": self.doc_type}],
                                             "with_url": True})
        metas = data.get("metas") or []
        if metas:
            self.raw.update({key: value for key, value in metas[0].items() if key not in ("doc_token",)})
            return dict(metas[0])
        return {}
