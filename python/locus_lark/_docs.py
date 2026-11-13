"""Documents (docx): read as Markdown, XML or blocks, edit, history,
images and files, plus everything cloud objects share (comments,
collaborators, sharing, export).

Edits go through the document's content API: Markdown for ordinary writing,
or DocxXML (``doc.xml()``, which carries block ids) for precise changes.
Block-level changes (``block.set_text``, ``block.delete``) use the block API.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from ._cloud import CloudObject
from ._core import LarkError, Resource, Service
from ._util import from_timestamp, read_file, token_of

BLOCK_TYPES = {
    1: "page", 2: "text", 3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4", 7: "heading5",
    8: "heading6", 9: "heading7", 10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered", 14: "code",
    15: "quote", 17: "todo", 18: "bitable", 19: "callout", 20: "chat_card", 21: "diagram", 22: "divider",
    23: "file", 24: "grid", 25: "grid_column", 26: "iframe", 27: "image", 28: "isv", 29: "mindnote",
    30: "sheet", 31: "table", 32: "table_cell", 33: "view", 34: "quote_container", 35: "task",
}
_TAG = re.compile(r"</?h>")
_ALIGN = {"left": 1, "center": 2, "right": 3}


def text_block(text: str, kind: str = "text") -> dict[str, Any]:
    """A paragraph-like block (``text``, ``heading1``…``heading9``, ``bullet``,
    ``ordered``, ``quote``, ``todo``) for the block API."""
    number = next((key for key, value in BLOCK_TYPES.items() if value == kind), 2)
    return {"block_type": number, kind: {"elements": [{"text_run": {"content": text}}]}}


def _elements_text(elements: Iterable[Mapping[str, Any]]) -> str:
    pieces = []
    for element in elements or []:
        if "text_run" in element:
            pieces.append(str(element["text_run"].get("content", "")))
        elif "mention_doc" in element:
            pieces.append(str(element["mention_doc"].get("title") or element["mention_doc"].get("url", "")))
        elif "mention_user" in element:
            pieces.append("@" + str(element["mention_user"].get("user_id", "")))
        elif "equation" in element:
            pieces.append(str(element["equation"].get("content", "")))
    return "".join(pieces)


class Block(Resource):
    """One block of a document. ``type`` is its kind (``text``,
    ``heading1``, ``bullet``, ``code``, ``image``, ``table``, …), ``text``
    its readable text and ``children`` the nested blocks."""

    _id_key = "block_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], document: "Document | None") -> None:
        super().__init__(lark, raw)
        self.document = document
        self.children: list[Block] = []
        self.parent: Block | None = None

    @property
    def type(self) -> str:
        number = self.raw.get("block_type")
        return BLOCK_TYPES.get(number, f"type{number}")

    @property
    def parent_id(self) -> str | None:
        return self.raw.get("parent_id")

    @property
    def child_ids(self) -> list[str]:
        return list(self.raw.get("children") or [])

    @property
    def body(self) -> dict[str, Any]:
        """The type-specific part of the block (``raw[type]``)."""
        return dict(self.raw.get(self.type) or {})

    @property
    def text(self) -> str:
        return _elements_text(self.body.get("elements") or [])

    @property
    def done(self) -> bool | None:
        """For todo blocks: whether it is checked."""
        return (self.body.get("style") or {}).get("done") if self.type == "todo" else None

    def _label(self) -> str:
        text = self.text.replace("\n", " ")
        return f" {self.type}" + (f" {text[:40]!r}" if text else "")

    def walk(self) -> Iterable["Block"]:
        """This block and every nested block, in document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def _require_document(self) -> "Document":
        if self.document is None:
            raise LarkError("This block does not belong to a document")
        return self.document

    async def set_text(self, text: str) -> None:
        """Replaces the text of a paragraph-like block."""
        document = self._require_document()
        await self.lark.request("PATCH", f"/open-apis/docx/v1/documents/{document.id}/blocks/{self.id}",
                                params={"document_revision_id": -1},
                                json={"update_text_elements": {"elements": [{"text_run": {"content": text}}]}})
        self.raw.setdefault(self.type, {})["elements"] = [{"text_run": {"content": text}}]

    async def add_children(self, blocks: list[dict[str, Any]], index: int | None = None) -> list["Block"]:
        """Adds raw blocks (see ``text_block``) inside this block."""
        document = self._require_document()
        data = await self.lark.request("POST", f"/open-apis/docx/v1/documents/{document.id}/blocks/{self.id}/children",
                                       params={"document_revision_id": -1}, json={"children": blocks, "index": index})
        return [Block(self.lark, item, document) for item in data.get("children") or []]

    async def delete(self) -> None:
        """Removes this block (and its children) from the document."""
        document = self._require_document()
        await document.delete_blocks(self)


def build_tree(blocks: list[Block]) -> list[Block]:
    """Links ``children``/``parent`` and returns the top-level blocks (the
    children of the page block, or the roots when there is none)."""
    by_id = {block.id: block for block in blocks}
    for block in blocks:
        block.children = [by_id[child] for child in block.child_ids if child in by_id]
        for child in block.children:
            child.parent = block
    roots = [block for block in blocks if block.parent is None]
    page = next((block for block in roots if block.type == "page"), None)
    return page.children if page is not None else roots


class HistoryVersion(Resource):
    """A saved version of a document; ``await doc.revert(version)`` restores it."""

    _id_key = "history_version_id"

    @property
    def revision_id(self) -> int | None:
        value = self.raw.get("revision_id")
        return int(value) if value is not None else None

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("edit_time") or self.raw.get("create_time"))

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def description(self) -> str:
        return str(self.raw.get("description") or "")

    @property
    def editor_ids(self) -> list[str]:
        editors = self.raw.get("editor_ids") or self.raw.get("editors") or []
        return [str(item.get("user_id") if isinstance(item, Mapping) else item) for item in editors]


def _block_id(block: Any) -> str:
    return block.id if isinstance(block, Block) else str(block)


class Document(CloudObject):
    """A docx document (``id`` is the ``document_id``)."""

    _id_key = "document_id"
    doc_type = "docx"

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    @property
    def revision(self) -> int | None:
        return self.raw.get("revision_id")

    async def refresh(self) -> "Document":
        data = await self.lark.request("GET", f"/open-apis/docx/v1/documents/{self.id}")
        self.raw.update(data.get("document") or {})
        return self

    async def _title(self) -> str:
        if not self.title:
            await self.refresh()
        return self.title or self.id

    # ── reading ─────────────────────────────────────────────────────────

    async def _fetch(self, format: str, revision: int | None, *, block_ids: bool = False,
                     styles: bool = False) -> str:
        body: dict[str, Any] = {"format": format, "export_option": {
            "export_block_id": block_ids, "export_style_attrs": styles, "export_cite_extra_data": styles}}
        if revision and revision > 0:
            body["revision_id"] = revision
        data = await self.lark.request("POST", f"/open-apis/docs_ai/v1/documents/{self.id}/fetch", json=body)
        document = data.get("document") or {}
        if document.get("title"):
            self.raw["title"] = document["title"]
        if document.get("revision_id") is not None:
            self.raw["revision_id"] = document["revision_id"]
        return str(document.get("content") or "")

    async def markdown(self, *, revision: int | None = None) -> str:
        """The content as Markdown."""
        return await self._fetch("markdown", revision)

    async def xml(self, *, revision: int | None = None, block_ids: bool = True, styles: bool = False) -> str:
        """The content as DocxXML: with ``block_ids`` every element carries
        the ``id`` that ``insert_after``, ``replace_blocks`` and the other
        block edits take; ``styles`` adds style attributes."""
        return await self._fetch("xml", revision, block_ids=block_ids, styles=styles)

    async def raw_text(self) -> str:
        """The plain text, without any formatting."""
        data = await self.lark.request("GET", f"/open-apis/docx/v1/documents/{self.id}/raw_content")
        return str(data.get("content") or "")

    async def all_blocks(self) -> list[Block]:
        """Every block in document order (the page block first)."""
        blocks = [Block(self.lark, item, self) async for item in self.lark.pages(
            "GET", f"/open-apis/docx/v1/documents/{self.id}/blocks", params={"document_revision_id": -1},
            page_size=500)]
        build_tree(blocks)
        return blocks

    async def blocks(self) -> list[Block]:
        """The top-level blocks, each with its nested ``children``."""
        return build_tree(await self.all_blocks())

    # ── editing ─────────────────────────────────────────────────────────

    async def _update(self, command: str, *, content: str | None = None, format: str = "markdown",
                      **fields: Any) -> dict[str, Any]:
        body = {"format": format, "command": command, "content": content, "revision_id": -1, **fields}
        return await self.lark.request("PUT", f"/open-apis/docs_ai/v1/documents/{self.id}", json=body)

    async def append(self, content: str, *, format: str = "markdown") -> dict[str, Any]:
        """Adds Markdown (or DocxXML with ``format="xml"``) at the end."""
        return await self._update("block_insert_after", content=content, format=format, block_id="-1")

    async def overwrite(self, content: str, *, format: str = "markdown") -> dict[str, Any]:
        """Replaces the whole content."""
        return await self._update("overwrite", content=content, format=format)

    async def replace(self, pattern: str, text: str) -> dict[str, Any]:
        """Replaces inline text matching ``pattern`` (simple text within a
        paragraph; use ``replace_blocks`` for whole paragraphs)."""
        return await self._update("str_replace", content=text, pattern=pattern)

    async def insert_after(self, block: Any, content: str, *, format: str = "markdown") -> dict[str, Any]:
        """Inserts content after a block (a Block or block id; ``"0"`` is the
        document start, ``"-1"`` its end)."""
        return await self._update("block_insert_after", content=content, format=format, block_id=_block_id(block))

    async def replace_blocks(self, block: Any, content: str, *, until: Any = None,
                             format: str = "markdown") -> dict[str, Any]:
        """Replaces a block, or the sibling range ``block``…``until``, with content."""
        if until is None:
            return await self._update("block_replace", content=content, format=format, block_id=_block_id(block))
        return await self._update("block_replace", content=content, format=format,
                                  start_block_id=_block_id(block), end_block_id=_block_id(until))

    async def delete_blocks(self, block: Any, *, until: Any = None) -> dict[str, Any]:
        """Deletes a block, several (a list), or the sibling range ``block``…``until``."""
        if until is not None:
            return await self._update("block_delete", start_block_id=_block_id(block), end_block_id=_block_id(until))
        ids = [_block_id(item) for item in block] if isinstance(block, (list, tuple)) else [_block_id(block)]
        return await self._update("block_delete", block_id=",".join(ids))

    async def move_blocks(self, blocks: Iterable[Any], *, after: Any) -> dict[str, Any]:
        return await self._update("block_move_after", block_id=_block_id(after),
                                  src_block_ids=",".join(_block_id(block) for block in blocks))

    async def copy_blocks(self, blocks: Iterable[Any], *, after: Any) -> dict[str, Any]:
        return await self._update("block_copy_insert_after", block_id=_block_id(after),
                                  src_block_ids=",".join(_block_id(block) for block in blocks))

    # ── images and files ────────────────────────────────────────────────

    async def _insert_media(self, kind: str, path: Any, index: int | None, after: Any, width: int | None,
                            height: int | None, align: str | None, caption: str | None) -> Block:
        from ._drive import upload_bytes

        root = f"/open-apis/docx/v1/documents/{self.id}/blocks/{self.id}"
        if after is not None:
            top = await self.blocks()
            ids = [block.id for block in top]
            target = _block_id(after)
            if target not in ids:
                raise LarkError("'after' must be a top-level block of this document")
            index = ids.index(target) + 1
        if index is None:
            page = await self.lark.request("GET", root, params={"document_revision_id": -1})
            index = len((page.get("block") or {}).get("children") or [])
        child = {"block_type": 23, "file": {}} if kind == "file" else {"block_type": 27, "image": {}}
        created = await self.lark.request("POST", root + "/children", params={"document_revision_id": -1},
                                          json={"children": [child], "index": index})
        block_raw = (created.get("children") or [{}])[0]
        block_id = block_raw.get("block_id")
        upload_node = block_id
        if kind == "file" and block_raw.get("children"):
            upload_node = block_raw["children"][0]
        name, data, mime = read_file(path)
        token = await upload_bytes(self.lark, "medias", data, name, mime,
                                   parent_type="docx_file" if kind == "file" else "docx_image", parent_node=upload_node,
                                   extra=f'{{"drive_route_token":"{self.id}"}}')
        if kind == "file":
            request = {"block_id": upload_node, "replace_file": {"token": token}}
        else:
            replace: dict[str, Any] = {"token": token, "width": width, "height": height,
                                       "align": _ALIGN.get(align) if align else None}
            if caption:
                replace["caption"] = {"content": caption}
            request = {"block_id": block_id, "replace_image": replace}
        await self.lark.request("PATCH", f"/open-apis/docx/v1/documents/{self.id}/blocks/batch_update",
                                params={"document_revision_id": -1}, json={"requests": [request]})
        return Block(self.lark, block_raw, self)

    async def insert_image(self, path: Any, *, after: Any = None, index: int | None = None, width: int | None = None,
                           height: int | None = None, align: str | None = None, caption: str | None = None) -> Block:
        """Inserts a local image after a top-level block (at the end by default)."""
        return await self._insert_media("image", path, index, after, width, height, align, caption)

    async def insert_file(self, path: Any, *, after: Any = None, index: int | None = None) -> Block:
        """Attaches a local file as a file block."""
        return await self._insert_media("file", path, index, after, None, None, None, None)

    # ── history ─────────────────────────────────────────────────────────

    async def history(self, *, limit: int = 20) -> list[HistoryVersion]:
        """Saved versions, newest first."""
        versions: list[HistoryVersion] = []
        params: dict[str, Any] = {"page_size": min(20, limit)}
        while len(versions) < limit:
            data = await self.lark.request("GET", f"/open-apis/docs_ai/v1/documents/{self.id}/histories", params=params)
            items = next((value for key, value in data.items() if isinstance(value, list)), [])
            versions.extend(HistoryVersion(self.lark, item) for item in items)
            if not data.get("has_more") or not data.get("page_token"):
                break
            params["page_token"] = data["page_token"]
        return versions[:limit]

    async def revert(self, version: Any, *, timeout: float = 120.0) -> dict[str, Any]:
        """Restores a saved version and waits until it is done; raises
        ``LarkError`` when it fails or fails for some blocks
        (``error.data["failed_block_tokens"]``)."""
        version_id = version.id if isinstance(version, HistoryVersion) else str(version)
        status = await self.lark.request("POST", f"/open-apis/docs_ai/v1/documents/{self.id}/history/revert",
                                         json={"history_version_id": version_id,
                                               "wait_timeout_ms": int(min(30.0, timeout) * 1000)})
        task_id = status.get("task_id")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            state = str(status.get("status") or "")
            if state == "done":
                return status
            if state in ("failed", "partial_failed"):
                raise LarkError(f"Restoring version {version_id} {state.replace('_', ' ')}", data=status)
            if not task_id or loop.time() > deadline:
                raise LarkError(f"Restoring version {version_id} did not finish in time", data=status)
            await asyncio.sleep(min(10.0, max(1.0, float(status.get("poll_after_ms") or 2000) / 1000)))
            status = {**status, **await self.lark.request(
                "GET", f"/open-apis/docs_ai/v1/documents/{self.id}/history/revert_status", params={"task_id": task_id})}


class SearchResult(Resource):
    """A search hit: ``title``, ``kind`` (docx, sheet, bitable, wiki, …) and
    ``url``; ``open()`` returns the object."""

    _id_key = "url"

    @property
    def title(self) -> str:
        return _TAG.sub("", str(self.raw.get("title_highlighted") or self.raw.get("title") or ""))

    @property
    def kind(self) -> str:
        meta = self.raw.get("result_meta") or {}
        kinds = meta.get("doc_types")
        if isinstance(kinds, list) and kinds:
            return str(kinds[0]).lower()
        return str(kinds or self.raw.get("entity_type") or "").lower()

    @property
    def url(self) -> str:
        return str((self.raw.get("result_meta") or {}).get("url") or "")

    @property
    def updated_at(self) -> str | None:
        return (self.raw.get("result_meta") or {}).get("update_time_iso")

    def _label(self) -> str:
        return f" {self.kind} {self.title[:40]!r}"

    @property
    def id(self) -> str:
        return self.url

    async def open(self) -> Any:
        return await self.lark.open(self.url)


class Docs(Service):
    async def open(self, document: Any) -> Document:
        """A document by link (docx or wiki) or ``document_id``."""
        token, kind, _ = token_of(document, ("docx",))
        if kind == "wiki":
            node = await self.lark.wiki.node(token)
            return await node.document()
        return await Document(self.lark, {"document_id": token}).refresh()

    def ref(self, document_id: str) -> Document:
        return Document(self.lark, {"document_id": document_id})

    async def create(self, title: str = "", markdown: str | None = None, *, folder: Any = None,
                     wiki: Any = None) -> Document:
        """Creates a document in a Drive folder (the root by default), or as a
        page under a WikiSpace or WikiNode, and writes ``markdown`` into it."""
        if wiki is not None:
            from ._wiki import WikiNode, WikiSpace

            if isinstance(wiki, WikiSpace):
                node = await wiki.create_node(title)
            elif isinstance(wiki, WikiNode):
                node = await wiki.create_child(title)
            else:
                raise TypeError("wiki must be a WikiSpace or WikiNode")
            document = await node.document()
        else:
            folder_token = getattr(folder, "token", None) or (str(folder) if folder else None)
            data = await self.lark.request("POST", "/open-apis/docx/v1/documents",
                                           json={"title": title, "folder_token": folder_token})
            document = Document(self.lark, data.get("document") or {})
        if markdown:
            await document.append(markdown)
        return document

    async def search(self, query: str, *, kinds: Iterable[str] = (), limit: int | None = 20) -> AsyncIterator[SearchResult]:
        """Documents, sheets, bases and wiki pages visible to the user that
        match ``query``; ``kinds`` narrows the types (``docx``, ``doc``,
        ``sheet``, ``bitable``, ``mindnote``, ``file``, ``slides``, ``wiki``)."""
        body: dict[str, Any] = {"query": query, "doc_filter": {}, "wiki_filter": {}}
        kinds = [kind.upper() for kind in kinds]
        if kinds:
            body["doc_filter"] = {"doc_types": kinds}
            body["wiki_filter"] = {"doc_types": kinds}
        count = 0
        page_token = None
        while True:
            if page_token:
                body["page_token"] = page_token
            body["page_size"] = 20 if limit is None else max(1, min(20, limit - count))
            data = await self.lark.request("POST", "/open-apis/search/v2/doc_wiki/search", json=body)
            for item in data.get("res_units") or []:
                yield SearchResult(self.lark, item)
                count += 1
                if limit is not None and count >= limit:
                    return
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                return

