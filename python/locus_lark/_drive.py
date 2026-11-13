"""Drive: folders and files, uploads, downloads and imports, plus
``lark.open(link)`` for any cloud object."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import zlib
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from ._cloud import CloudObject
from ._core import LarkError, Service
from ._util import from_timestamp, parse_link, read_file, write_file

_SINGLE_UPLOAD_LIMIT = 20 * 1024 * 1024
_IMPORT_KINDS = {
    ".docx": "docx", ".doc": "docx", ".md": "docx", ".markdown": "docx", ".txt": "docx", ".html": "docx",
    ".xlsx": "sheet", ".xls": "sheet", ".csv": "sheet",
}
_TASK_TIMEOUT = 300.0


class DriveFile(CloudObject):
    """A file or document in Drive (``id`` is its token; ``kind`` is
    ``file``, ``docx``, ``sheet``, ``bitable``, ``folder``, …)."""

    _id_key = "token"

    @property
    def doc_type(self) -> str:  # type: ignore[override]
        return str(self.raw.get("type") or "file")

    @property
    def kind(self) -> str:
        return self.doc_type

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or self.raw.get("title") or "")

    @property
    def parent_token(self) -> str | None:
        return self.raw.get("parent_token")

    @property
    def modified_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("modified_time"))

    def open(self) -> Any:
        """The typed object: Document, Spreadsheet, Base, Folder or this file."""
        return open_typed(self.lark, self.token, self.doc_type, self.raw)

    async def download(self, path: Any) -> Path:
        """Saves an uploaded file (``kind == "file"``) to ``path`` (a file or a folder)."""
        data = await self.lark.request("GET", f"/open-apis/drive/v1/files/{self.token}/download")
        target = Path(path).expanduser()
        if target.is_dir():
            target = target / (self.name or self.token)
        return write_file(target, data)


class Folder(DriveFile):
    """A Drive folder."""

    @property
    def doc_type(self) -> str:  # type: ignore[override]
        return "folder"

    async def files(self, *, limit: int | None = None) -> AsyncIterator[DriveFile]:
        async for item in self.lark.pages("GET", "/open-apis/drive/v1/files",
                                          params={"folder_token": self.token or None, "user_id_type": "open_id"},
                                          page_size=200, limit=limit):
            yield open_typed(self.lark, item.get("token"), item.get("type"), item)

    async def create_folder(self, name: str) -> "Folder":
        data = await self.lark.request("POST", "/open-apis/drive/v1/files/create_folder",
                                       json={"name": name, "folder_token": self.token})
        return Folder(self.lark, {"token": data.get("token"), "name": name, "url": data.get("url"), "type": "folder"})

    async def upload(self, path: Any, name: str | None = None) -> DriveFile:
        return await self.lark.drive.upload(path, folder=self, name=name)


def open_typed(lark: Any, token: Any, kind: Any, raw: Mapping[str, Any] | None = None) -> Any:
    """The object for a token of a Drive type."""
    raw = dict(raw or {})
    kind = str(kind or raw.get("type") or "file")
    if kind == "docx":
        from ._docs import Document

        return Document(lark, {**raw, "document_id": token, "title": raw.get("title") or raw.get("name")})
    if kind == "sheet":
        from ._sheets import Spreadsheet

        return Spreadsheet(lark, {**raw, "spreadsheet_token": token, "title": raw.get("title") or raw.get("name")})
    if kind == "bitable":
        from ._bitable import Base

        return Base(lark, {**raw, "app_token": token, "name": raw.get("name") or raw.get("title")})
    if kind == "folder":
        return Folder(lark, {**raw, "token": token, "type": "folder"})
    return DriveFile(lark, {**raw, "token": token, "type": kind})


class Drive(Service):
    async def root(self) -> Folder:
        """The user's root folder ("My Space")."""
        data = await self.lark.request("GET", "/open-apis/drive/explorer/v2/root_folder/meta")
        return Folder(self.lark, {"token": data.get("token"), "name": "", "type": "folder", **data})

    def folder(self, token: str) -> Folder:
        return Folder(self.lark, {"token": token, "type": "folder"})

    def file(self, token: str, kind: str = "file") -> DriveFile:
        return open_typed(self.lark, token, kind)

    async def upload(self, path: Any, *, folder: Any = None, name: str | None = None) -> DriveFile:
        """Uploads a local file into a folder (the root folder by default)."""
        folder_token = getattr(folder, "token", None) or (str(folder) if folder else (await self.root()).token)
        file_name, data, mime = read_file(path)
        file_name = name or file_name
        token = await upload_bytes(self.lark, "files", data, file_name, mime, parent_type="explorer",
                                   parent_node=folder_token)
        return DriveFile(self.lark, {"token": token, "name": file_name, "type": "file", "parent_token": folder_token})

    async def download(self, token: str, path: Any) -> Path:
        return await DriveFile(self.lark, {"token": token, "type": "file"}).download(path)

    async def import_file(self, path: Any, *, kind: str | None = None, folder: Any = None,
                          name: str | None = None) -> Any:
        """Imports a local Word, Markdown, text, HTML, Excel or CSV file as a
        cloud document (``kind`` ``docx``, ``sheet`` or ``bitable``) in a
        folder (the root by default). Returns the new Document, Spreadsheet or Base."""
        file_name, data, mime = read_file(path)
        extension = Path(file_name).suffix.lower()
        kind = kind or _IMPORT_KINDS.get(extension)
        if kind is None:
            raise ValueError(f"Cannot import {extension or 'this file'}; pass kind=")
        ext = extension.lstrip(".") or "txt"
        token = await upload_bytes(self.lark, "medias", data, file_name, mime, parent_type="ccm_import_open",
                                   parent_node="", extra=json.dumps({"obj_type": kind, "file_extension": ext}))
        folder_token = getattr(folder, "token", None) or (str(folder) if folder else "")
        task = await self.lark.request("POST", "/open-apis/drive/v1/import_tasks", json={
            "file_extension": ext, "file_token": token, "type": kind, "file_name": name or Path(file_name).stem,
            "point": {"mount_type": 1, "mount_key": folder_token},
        })
        ticket = task["ticket"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _TASK_TIMEOUT
        while True:
            result = (await self.lark.request("GET", f"/open-apis/drive/v1/import_tasks/{ticket}")).get("result") or {}
            status = result.get("job_status")
            if status == 0 and result.get("token"):
                return open_typed(self.lark, result["token"], result.get("type") or kind,
                                  {"url": result.get("url"), "title": name or Path(file_name).stem})
            if status not in (None, 1, 2):
                raise LarkError(f"Import failed: {result.get('job_error_msg') or status}", data=result)
            if loop.time() > deadline:
                raise LarkError("Import did not finish in time", data=result)
            await asyncio.sleep(1.5)

    async def search(self, query: str, *, limit: int | None = 20) -> AsyncIterator[Any]:
        """Documents, sheets, bases and wiki pages matching ``query`` (same as ``docs.search``)."""
        async for item in self.lark.docs.search(query, limit=limit):
            yield item


async def upload_bytes(lark: Any, kind: str, data: bytes, name: str, mime: str, *, parent_type: str,
                       parent_node: str, extra: str | None = None) -> str:
    """Uploads to Drive ``files`` or ``medias`` (chunked above 20 MB); returns the file token."""
    base = f"/open-apis/drive/v1/{kind}"
    fields: dict[str, Any] = {"file_name": name, "parent_type": parent_type, "parent_node": parent_node,
                              "size": str(len(data))}
    if extra is not None:
        fields["extra"] = extra
    if len(data) <= _SINGLE_UPLOAD_LIMIT:
        fields["checksum"] = str(zlib.adler32(data))
        reply = await lark.request("POST", f"{base}/upload_all", data=fields, files={"file": (name, data, mime)})
        return reply["file_token"]
    prepared = await lark.request("POST", f"{base}/upload_prepare", json={**fields, "size": len(data)})
    upload_id, block_size = prepared["upload_id"], int(prepared["block_size"])
    count = 0
    for seq, start in enumerate(range(0, len(data), block_size)):
        chunk = data[start:start + block_size]
        await lark.request("POST", f"{base}/upload_part",
                           data={"upload_id": upload_id, "seq": str(seq), "size": str(len(chunk)),
                                 "checksum": str(zlib.adler32(chunk))},
                           files={"file": (name, chunk, "application/octet-stream")})
        count += 1
    reply = await lark.request("POST", f"{base}/upload_finish", json={"upload_id": upload_id, "block_num": count})
    return reply["file_token"]


async def open_link(lark: Any, value: Any) -> Any:
    """The object behind a Feishu / Lark link (wiki links open the page's
    document, sheet or base; ``WikiNode`` for other wiki pages)."""
    link = parse_link(str(value))
    if link is None:
        raise ValueError(f"Not a Feishu / Lark link: {value}")
    if link.kind == "wiki":
        node = await lark.wiki.node(link.token)
        return node.open()
    if link.kind == "doc":
        raise LarkError("Old-style documents (/docs/) are not supported; open a docx document")
    target = open_typed(lark, link.token, link.kind, {"url": str(value)})
    for key, name in (("sheet", "_sheet"), ("table", "_table"), ("view", "_view")):
        if key in link.query:
            target.raw[name] = link.query[key]
    return target
