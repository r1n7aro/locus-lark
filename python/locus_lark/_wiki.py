"""Wiki: spaces, the page tree, members.

A ``WikiNode`` is a page in a space's tree; the content lives in the object
it points to (``node.open()``: a Document, Spreadsheet or Base).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from ._core import LarkError, Resource, Service
from ._util import token_of
from ._cloud import member_target

_ROLES = ("member", "admin")


class WikiNode(Resource):
    """A wiki page (``id`` is the ``node_token``). ``kind`` is the type of
    the object it holds (``docx``, ``sheet``, ``bitable``, ``mindnote``,
    ``file``, …) and ``obj_token`` that object's token."""

    _id_key = "node_token"

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    @property
    def kind(self) -> str:
        return str(self.raw.get("obj_type") or "")

    @property
    def obj_token(self) -> str:
        return str(self.raw.get("obj_token") or "")

    @property
    def space_id(self) -> str:
        return str(self.raw.get("space_id") or "")

    @property
    def space(self) -> "WikiSpace":
        return WikiSpace(self.lark, {"space_id": self.space_id})

    @property
    def parent_token(self) -> str | None:
        return self.raw.get("parent_node_token") or None

    @property
    def has_children(self) -> bool:
        return bool(self.raw.get("has_child"))

    @property
    def is_shortcut(self) -> bool:
        return self.raw.get("node_type") == "shortcut"

    async def refresh(self) -> "WikiNode":
        data = await self.lark.request("GET", "/open-apis/wiki/v2/spaces/get_node",
                                       params={"token": self.id, "obj_type": "wiki"})
        self.raw.update(data.get("node") or {})
        return self

    async def parent(self) -> "WikiNode | None":
        if not self.parent_token:
            return None
        return await WikiNode(self.lark, {"node_token": self.parent_token}).refresh()

    # ── tree ────────────────────────────────────────────────────────────

    async def children(self) -> AsyncIterator["WikiNode"]:
        async for node in self.space.nodes(parent=self):
            yield node

    async def walk(self, *, depth: int | None = None) -> AsyncIterator["WikiNode"]:
        """Every page below this one, depth first (``depth`` limits the levels)."""
        async for node in _walk(self.space, self, depth):
            yield node

    async def create_child(self, title: str, *, kind: str = "docx", markdown: str | None = None) -> "WikiNode":
        """Adds a page below this one (``kind`` ``docx``, ``sheet``, ``bitable``, ``mindnote``)."""
        return await self.space.create_node(title, parent=self, kind=kind, markdown=markdown)

    # ── content ─────────────────────────────────────────────────────────

    def open(self) -> Any:
        """The object this page holds: Document, Spreadsheet, Base, or this node."""
        from ._drive import open_typed

        if self.kind in ("docx", "sheet", "bitable"):
            target = open_typed(self.lark, self.obj_token, self.kind, {"title": self.title})
            target.raw["wiki_node"] = self.id
            return target
        return self

    async def document(self) -> Any:
        """The Document of a docx page."""
        if not self.kind:
            await self.refresh()
        if self.kind != "docx":
            raise LarkError(f"Wiki page {self.title!r} holds a {self.kind}, not a document")
        return self.open()

    # ── changes ─────────────────────────────────────────────────────────

    async def rename(self, title: str) -> None:
        await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.space_id}/nodes/{self.id}/update_title",
                                json={"title": title})
        self.raw["title"] = title

    async def move(self, *, parent: "WikiNode | str | None" = None, space: "WikiSpace | str | None" = None) -> "WikiNode":
        """Moves under another page and/or into another space (top level when no parent)."""
        body = {"target_parent_token": parent.id if isinstance(parent, WikiNode) else parent,
                "target_space_id": space.id if isinstance(space, WikiSpace) else (space or self.space_id)}
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.space_id}/nodes/{self.id}/move",
                                       json=body)
        self.raw.update(data.get("node") or {})
        return self

    async def copy(self, *, title: str | None = None, parent: "WikiNode | str | None" = None,
                   space: "WikiSpace | str | None" = None) -> "WikiNode":
        body = {"target_parent_token": parent.id if isinstance(parent, WikiNode) else parent,
                "target_space_id": space.id if isinstance(space, WikiSpace) else (space or self.space_id),
                "title": title}
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.space_id}/nodes/{self.id}/copy",
                                       json=body)
        return WikiNode(self.lark, data.get("node") or {})

    async def add_shortcut(self, *, parent: "WikiNode | str | None" = None,
                           space: "WikiSpace | str | None" = None) -> "WikiNode":
        """Links this page from another place in the same or another space."""
        target_space = space.id if isinstance(space, WikiSpace) else (space or self.space_id)
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{target_space}/nodes", json={
            "obj_type": self.kind, "node_type": "shortcut", "origin_node_token": self.id,
            "parent_node_token": parent.id if isinstance(parent, WikiNode) else parent,
        })
        return WikiNode(self.lark, data.get("node") or {})


async def _walk(space: "WikiSpace", parent: WikiNode | None, depth: int | None) -> AsyncIterator[WikiNode]:
    async for node in space.nodes(parent=parent):
        yield node
        if node.has_children and (depth is None or depth > 1):
            async for child in _walk(space, node, None if depth is None else depth - 1):
                yield child


class WikiSpace(Resource):
    """A knowledge space (``id`` is the ``space_id``)."""

    _id_key = "space_id"

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def description(self) -> str:
        return str(self.raw.get("description") or "")

    @property
    def visibility(self) -> str | None:
        return self.raw.get("visibility")

    async def refresh(self) -> "WikiSpace":
        data = await self.lark.request("GET", f"/open-apis/wiki/v2/spaces/{self.id}")
        self.raw.update(data.get("space") or {})
        return self

    async def nodes(self, *, parent: WikiNode | str | None = None, limit: int | None = None) -> AsyncIterator[WikiNode]:
        """Top-level pages, or the children of ``parent``."""
        parent_token = parent.id if isinstance(parent, WikiNode) else parent
        async for item in self.lark.pages("GET", f"/open-apis/wiki/v2/spaces/{self.id}/nodes",
                                          params={"parent_node_token": parent_token}, page_size=50, limit=limit):
            yield WikiNode(self.lark, item)

    async def walk(self, *, depth: int | None = None) -> AsyncIterator[WikiNode]:
        """Every page of the space, depth first."""
        async for node in _walk(self, None, depth):
            yield node

    async def find(self, title: str) -> WikiNode:
        """The first page titled ``title`` anywhere in the space."""
        async for node in self.walk():
            if node.title == title:
                return node
        raise LookupError(f"No page titled {title!r} in {self.name or self.id}")

    async def create_node(self, title: str, *, parent: WikiNode | str | None = None, kind: str = "docx",
                          markdown: str | None = None) -> WikiNode:
        """Adds a page (a new ``docx`` by default) at the top level or under ``parent``;
        ``markdown`` fills a new document."""
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.id}/nodes", json={
            "obj_type": kind, "node_type": "origin", "title": title,
            "parent_node_token": parent.id if isinstance(parent, WikiNode) else parent,
        })
        node = WikiNode(self.lark, data.get("node") or {})
        if markdown:
            if kind != "docx":
                raise ValueError("markdown only fills docx pages")
            await (await node.document()).append(markdown)
        return node

    async def move_in(self, document: Any, *, parent: WikiNode | str | None = None) -> dict[str, Any]:
        """Moves a Drive document (Document, Spreadsheet, Base or file) into this space."""
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.id}/nodes/move_docs_to_wiki", json={
            "obj_type": getattr(document, "doc_type", "docx"), "obj_token": getattr(document, "token", None) or str(document),
            "parent_wiki_token": parent.id if isinstance(parent, WikiNode) else parent,
        })
        return data

    # ── members ─────────────────────────────────────────────────────────

    async def members(self, *, limit: int | None = None) -> AsyncIterator[dict[str, Any]]:
        async for item in self.lark.pages("GET", f"/open-apis/wiki/v2/spaces/{self.id}/members", page_size=50, limit=limit):
            yield item

    async def add_member(self, who: Any, role: str = "member", *, notify: bool = False) -> dict[str, Any]:
        """Adds a person, chat or department as ``member`` or ``admin``."""
        if role not in _ROLES:
            raise ValueError("role must be 'member' or 'admin'")
        kind, member_id = member_target(who)
        data = await self.lark.request("POST", f"/open-apis/wiki/v2/spaces/{self.id}/members",
                                       params={"need_notification": notify},
                                       json={"member_type": kind, "member_id": member_id, "member_role": role})
        return dict(data.get("member") or data)

    async def remove_member(self, who: Any, role: str = "member") -> None:
        kind, member_id = member_target(who)
        await self.lark.request("DELETE", f"/open-apis/wiki/v2/spaces/{self.id}/members/{member_id}",
                                json={"member_type": kind, "member_role": role})


class Wiki(Service):
    async def spaces(self, *, limit: int | None = None) -> AsyncIterator[WikiSpace]:
        """Spaces the user can see."""
        async for item in self.lark.pages("GET", "/open-apis/wiki/v2/spaces", page_size=50, limit=limit):
            yield WikiSpace(self.lark, item)

    async def space(self, name_or_id: str) -> WikiSpace:
        """A space by id, or the first space with this name."""
        if name_or_id.isdigit():
            return await WikiSpace(self.lark, {"space_id": name_or_id}).refresh()
        async for space in self.spaces():
            if space.name == name_or_id:
                return space
        raise LookupError(f"No wiki space named {name_or_id!r}")

    async def node(self, link_or_token: Any) -> WikiNode:
        """A page by wiki link or node token."""
        token, _, _ = token_of(link_or_token, ("wiki",))
        return await WikiNode(self.lark, {"node_token": token}).refresh()

    async def node_of(self, document: Any) -> WikiNode:
        """The wiki page that holds a Document, Spreadsheet or Base."""
        data = await self.lark.request("GET", "/open-apis/wiki/v2/spaces/get_node",
                                       params={"token": getattr(document, "token", None) or str(document),
                                               "obj_type": getattr(document, "doc_type", "docx")})
        return WikiNode(self.lark, data.get("node") or {})

    async def create_space(self, name: str, *, description: str | None = None) -> WikiSpace:
        data = await self.lark.request("POST", "/open-apis/wiki/v2/spaces", json={"name": name, "description": description})
        return WikiSpace(self.lark, data.get("space") or {})

    def space_ref(self, space_id: str) -> WikiSpace:
        return WikiSpace(self.lark, {"space_id": space_id})

    def node_ref(self, raw: Mapping[str, Any]) -> WikiNode:
        return WikiNode(self.lark, raw)
