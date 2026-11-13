"""Conversations: chats (groups and direct chats), messages, reactions,
pins, announcements.

``Chat`` and ``Message`` objects navigate to each other (``message.chat``,
``chat.messages()``). Lists are async iterators that page on demand.
Operations the platform only allows for the app's bot (editing a text
message, urgent notifications, merging messages into one forward) run as the
bot automatically and act on messages the bot sent.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid as _uuid
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._core import LarkAuthError, LarkError, LarkScopeError, Resource, Service

if TYPE_CHECKING:
    from ._docs import Block
from ._util import (
    from_timestamp,
    message_content,
    plain_text,
    read_file,
    receive_target,
    to_seconds,
    user_open_id,
    write_file,
)
from ._contact import User

_MESSAGE_PAGE = 50
_WITH_NAMES = {"with_sender_name": True, "user_id_type": "open_id"}
_FILE_TYPES = {".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
               ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt"}


def to_iso(value: Any) -> str | None:
    seconds = to_seconds(value)
    if seconds is None:
        return None
    return _dt.datetime.fromtimestamp(int(seconds)).astimezone().isoformat(timespec="seconds")


async def _content(lark: Any, text: str | None, *, markdown: str | None = None, post: Mapping[str, Any] | None = None,
                   card: Mapping[str, Any] | None = None, image: Any = None, file: Any = None,
                   content: Any = None, msg_type: str | None = None) -> tuple[str, str]:
    """``(msg_type, content)``; local images and files are uploaded first."""
    image_key = file_key = None
    if image is not None:
        image_key = image if isinstance(image, str) and image.startswith("img_") else await upload_image(lark, image)
    if file is not None:
        file_key = file if isinstance(file, str) and file.startswith("file_") else await upload_file(lark, file)
    return message_content(text, markdown=markdown, post=post, card=card, image_key=image_key, file_key=file_key,
                           content=content, msg_type=msg_type)


async def upload_image(lark: Any, path: Any) -> str:
    """Uploads a local image for messages; returns its ``image_key``."""
    name, data, mime = read_file(path)
    reply = await lark.request("POST", "/open-apis/im/v1/images", data={"image_type": "message"},
                               files={"image": (name, data, mime)})
    return reply["image_key"]


async def upload_file(lark: Any, path: Any) -> str:
    """Uploads a local file for messages; returns its ``file_key``."""
    name, data, mime = read_file(path)
    file_type = _FILE_TYPES.get(Path(name).suffix.lower(), "stream")
    reply = await lark.request("POST", "/open-apis/im/v1/files", data={"file_type": file_type, "file_name": name},
                               files={"file": (name, data, mime)})
    return reply["file_key"]


class Reaction(Resource):
    """An emoji reaction on a message."""

    _id_key = "reaction_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], message: "Message") -> None:
        super().__init__(lark, raw)
        self.message = message

    @property
    def emoji(self) -> str:
        return str((self.raw.get("reaction_type") or {}).get("emoji_type") or "")

    @property
    def operator_id(self) -> str | None:
        return (self.raw.get("operator") or {}).get("operator_id")

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("action_time"))

    def _label(self) -> str:
        return f" {self.emoji}"

    async def remove(self) -> None:
        await self.lark.request("DELETE", f"/open-apis/im/v1/messages/{self.message.id}/reactions/{self.id}")


class Pin(Resource):
    """A message pinned in a chat."""

    _id_key = "message_id"

    @property
    def chat_id(self) -> str | None:
        return self.raw.get("chat_id")

    @property
    def pinned_by(self) -> str | None:
        return self.raw.get("operator_id")

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("create_time"))

    async def message(self) -> "Message":
        return await self.lark.messages.get(self.id)

    async def remove(self) -> None:
        await self.lark.request("DELETE", f"/open-apis/im/v1/pins/{self.id}")


class Message(Resource):
    """One message. ``text`` is a readable rendering of ``content``, the
    message body as JSON (``type`` tells its kind: text, post, image,
    file, interactive, …)."""

    _id_key = "message_id"

    @property
    def type(self) -> str:
        return str(self.raw.get("msg_type") or "")

    @property
    def content(self) -> dict[str, Any]:
        body = (self.raw.get("body") or {}).get("content")
        if isinstance(body, str):
            try:
                value = json.loads(body)
            except json.JSONDecodeError:
                return {"text": body}
            return value if isinstance(value, dict) else {"value": value}
        return body or {}

    @property
    def text(self) -> str:
        text = plain_text(self.type, self.content)
        for mention in self.mentions:
            key, name = mention.get("key"), mention.get("name")
            if key and name:
                text = text.replace(key, "@" + name)
        return text

    @property
    def mentions(self) -> list[dict[str, Any]]:
        return list(self.raw.get("mentions") or [])

    @property
    def chat_id(self) -> str:
        return str(self.raw.get("chat_id") or "")

    @property
    def chat(self) -> "Chat":
        return Chat(self.lark, {"chat_id": self.chat_id})

    @property
    def sender(self) -> User:
        """The sender; for a bot, ``raw['sender_type'] == 'app'``."""
        sender = self.raw.get("sender") or {}
        return User(self.lark, {"open_id": sender.get("id"), "name": sender.get("name") or self.raw.get("sender_name"),
                                "sender_type": sender.get("sender_type"), "id_type": sender.get("id_type")})

    @property
    def sent_by_bot(self) -> bool:
        return (self.raw.get("sender") or {}).get("sender_type") == "app"

    @property
    def created_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("create_time"))

    @property
    def updated_at(self) -> _dt.datetime | None:
        return from_timestamp(self.raw.get("update_time"))

    @property
    def edited(self) -> bool:
        return bool(self.raw.get("updated"))

    @property
    def recalled(self) -> bool:
        return bool(self.raw.get("deleted"))

    @property
    def thread_id(self) -> str | None:
        return self.raw.get("thread_id")

    @property
    def parent_id(self) -> str | None:
        """The message this one replies to."""
        return self.raw.get("parent_id")

    @property
    def root_id(self) -> str | None:
        return self.raw.get("root_id")

    def _label(self) -> str:
        text = self.text.replace("\n", " ")
        return f" {text[:40]!r}" if text else f" {self.type}"

    async def refresh(self) -> "Message":
        data = await self.lark.request("GET", f"/open-apis/im/v1/messages/{self.id}", params=_WITH_NAMES)
        items = data.get("items") or []
        if items:
            self.raw.update(items[0])
        return self

    # ── replies and threads ─────────────────────────────────────────────

    async def reply(self, text: str | None = None, *, in_thread: bool = False, uuid: str | None = None,
                    **content: Any) -> "Message":
        """Replies with the arguments of ``Chat.send``; ``in_thread`` replies in a thread."""
        msg_type, body = await _content(self.lark, text, **content)
        data = await self.lark.request("POST", f"/open-apis/im/v1/messages/{self.id}/reply",
                                       json={"msg_type": msg_type, "content": body, "reply_in_thread": in_thread,
                                             "uuid": uuid or _uuid.uuid4().hex})
        return Message(self.lark, data)

    async def thread(self, *, order: str = "asc", limit: int | None = None) -> AsyncIterator["Message"]:
        """Messages of this message's thread."""
        thread_id = self.thread_id or (await self.refresh()).thread_id
        if not thread_id:
            return
        async for message in _list_messages(self.lark, "thread", thread_id, order=order, limit=limit):
            yield message

    # ── changes ─────────────────────────────────────────────────────────

    async def edit(self, text: str | None = None, *, markdown: str | None = None,
                   post: Mapping[str, Any] | None = None) -> "Message":
        """Changes a text or post message the bot sent (runs as the bot)."""
        msg_type, body = message_content(text, markdown=markdown, post=post)
        data = await self.lark.bot.request("PUT", f"/open-apis/im/v1/messages/{self.id}",
                                           json={"msg_type": msg_type, "content": body})
        if data:
            self.raw.update(data)
        return self

    async def update_card(self, card: Mapping[str, Any]) -> None:
        """Replaces the content of a card message this identity sent."""
        await self.lark.request("PATCH", f"/open-apis/im/v1/messages/{self.id}",
                                json={"content": json.dumps(card, ensure_ascii=False)})

    async def recall(self) -> None:
        await self.lark.request("DELETE", f"/open-apis/im/v1/messages/{self.id}")
        self.raw["deleted"] = True

    async def forward(self, to: Any) -> "Message":
        """Forwards to a Chat, User, chat id, user id or email."""
        id_type, receive_id = receive_target(to)
        data = await self.lark.request("POST", f"/open-apis/im/v1/messages/{self.id}/forward",
                                       params={"receive_id_type": id_type, "uuid": _uuid.uuid4().hex},
                                       json={"receive_id": receive_id})
        return Message(self.lark, data)

    # ── reactions, pins, urgent, read receipts ──────────────────────────

    async def react(self, emoji: str = "THUMBSUP") -> Reaction:
        """Adds an emoji reaction (``THUMBSUP``, ``OK``, ``DONE``, ``SMILE``, …)."""
        data = await self.lark.request("POST", f"/open-apis/im/v1/messages/{self.id}/reactions",
                                       json={"reaction_type": {"emoji_type": emoji}})
        return Reaction(self.lark, data, self)

    async def reactions(self, emoji: str | None = None, *, limit: int | None = None) -> AsyncIterator[Reaction]:
        async for item in self.lark.pages("GET", f"/open-apis/im/v1/messages/{self.id}/reactions",
                                          params={"reaction_type": emoji, "user_id_type": "open_id"},
                                          page_size=50, limit=limit):
            yield Reaction(self.lark, item, self)

    async def pin(self) -> Pin:
        data = await self.lark.request("POST", "/open-apis/im/v1/pins", json={"message_id": self.id})
        return Pin(self.lark, data.get("pin") or {"message_id": self.id, "chat_id": self.chat_id})

    async def unpin(self) -> None:
        await self.lark.request("DELETE", f"/open-apis/im/v1/pins/{self.id}")

    async def urgent(self, users: Iterable[Any], *, via: str = "app") -> list[str]:
        """Sends an urgent notification for a message the bot sent to
        ``users`` in the chat, ``via`` ``app``, ``sms`` or ``phone`` (runs as
        the bot). Returns the ids that could not be notified."""
        if via not in ("app", "sms", "phone"):
            raise ValueError("via must be 'app', 'sms' or 'phone'")
        data = await self.lark.bot.request("PATCH", f"/open-apis/im/v1/messages/{self.id}/urgent_{via}",
                                           params={"user_id_type": "open_id"},
                                           json={"user_id_list": [user_open_id(user) for user in users]})
        return list(data.get("invalid_user_id_list") or [])

    async def read_by(self, *, limit: int | None = None) -> AsyncIterator[User]:
        """Who has read a message this identity sent."""
        async for item in self.lark.pages("GET", f"/open-apis/im/v1/messages/{self.id}/read_users",
                                          params={"user_id_type": "open_id"}, page_size=100, limit=limit):
            yield User(self.lark, {"open_id": item.get("user_id"), "read_at": item.get("timestamp")})

    # ── attachments ─────────────────────────────────────────────────────

    def resource_keys(self) -> list[tuple[str, str]]:
        """``(key, kind)`` of the images and files in this message."""
        content, keys = self.content, []
        if content.get("image_key"):
            keys.append((content["image_key"], "image"))
        if content.get("file_key"):
            keys.append((content["file_key"], "file"))
        for paragraph in (content.get("content") or []):
            for element in paragraph or []:
                if element.get("tag") == "img" and element.get("image_key"):
                    keys.append((element["image_key"], "image"))
                if element.get("tag") == "media" and element.get("file_key"):
                    keys.append((element["file_key"], "file"))
        return keys

    async def download(self, path: Any, key: str | None = None) -> Path:
        """Saves an image or file of this message (the first one by default)
        to ``path`` (a file, or a folder to keep the original name)."""
        keys = self.resource_keys()
        if key is None:
            if not keys:
                raise LookupError("This message has no image or file")
            key, kind = keys[0]
        else:
            kind = dict(keys).get(key, "file")
        data = await self.lark.request("GET", f"/open-apis/im/v1/messages/{self.id}/resources/{key}",
                                       params={"type": kind})
        target = Path(path).expanduser()
        if target.is_dir():
            name = self.content.get("file_name") or f"{key}.{'png' if kind == 'image' else 'bin'}"
            target = target / name
        return write_file(target, data if isinstance(data, bytes) else json.dumps(data).encode())


async def _list_messages(lark: Any, container_type: str, container_id: str, *, since: Any = None, until: Any = None,
                         order: str = "asc", limit: int | None = None) -> AsyncIterator[Message]:
    params = {
        "container_id_type": container_type,
        "container_id": container_id,
        "start_time": to_seconds(since),
        "end_time": to_seconds(until),
        "sort_type": "ByCreateTimeDesc" if order == "desc" else "ByCreateTimeAsc",
        **_WITH_NAMES,
    }
    async for item in lark.pages("GET", "/open-apis/im/v1/messages", params=params, page_size=_MESSAGE_PAGE, limit=limit):
        yield Message(lark, item)


class Announcement:
    """The announcement of a chat. Current chats keep it as a document of
    blocks (``blocks()``, ``text()``, ``append()``, ``clear()``); chats with
    the older announcement only support ``text()``."""

    def __init__(self, chat: "Chat") -> None:
        self.chat = chat
        self.lark = chat.lark

    def __repr__(self) -> str:
        return f"<Announcement of {self.chat.id}>"

    @property
    def _base(self) -> str:
        return f"/open-apis/docx/v1/chats/{self.chat.id}/announcement"

    async def info(self) -> dict[str, Any]:
        return await self.lark.request("GET", self._base)

    async def _items(self) -> list[dict[str, Any]]:
        return [item async for item in self.lark.pages("GET", self._base + "/blocks", params={"revision_id": -1},
                                                      page_size=500)]

    @staticmethod
    def _root(items: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The page block that holds the paragraphs."""
        return next((item for item in items if item.get("block_type") == 1), None)

    async def blocks(self) -> list["Block"]:
        """The paragraphs and other top-level blocks, with nested children."""
        from ._docs import Block, build_tree

        return build_tree([Block(self.lark, item, None) for item in await self._items()])

    async def text(self) -> str:
        try:
            blocks = await self.blocks()
        except (LarkAuthError, LarkScopeError):
            raise
        except LarkError:
            # Chats that still have the older announcement.
            data = await self.lark.request("GET", f"/open-apis/im/v1/chats/{self.chat.id}/announcement")
            return str(data.get("content") or "")
        return "\n".join(block.text for block in blocks if block.text)

    async def append(self, text: str) -> None:
        """Adds paragraphs (one per line) at the end."""
        from ._docs import text_block

        root = self._root(await self._items())
        root_id = root.get("block_id") if root else self.chat.id
        children = [text_block(line) for line in text.split("\n")]
        await self.lark.request("POST", f"{self._base}/blocks/{root_id}/children", params={"revision_id": -1},
                                json={"children": children})

    async def clear(self) -> None:
        """Removes every paragraph."""
        root = self._root(await self._items())
        count = len((root or {}).get("children") or [])
        if root and count:
            await self.lark.request("DELETE", f"{self._base}/blocks/{root['block_id']}/children/batch_delete",
                                    params={"revision_id": -1}, json={"start_index": 0, "end_index": count})

    async def set(self, text: str) -> None:
        """Replaces the whole announcement."""
        await self.clear()
        if text:
            await self.append(text)


class Chat(Resource):
    """A group or a direct chat (``id`` is the ``chat_id``)."""

    _id_key = "chat_id"

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def description(self) -> str:
        return str(self.raw.get("description") or "")

    @property
    def owner_id(self) -> str | None:
        return self.raw.get("owner_id")

    @property
    def is_direct(self) -> bool:
        return self.raw.get("chat_mode") == "p2p" or self.raw.get("chat_type") == "p2p"

    @property
    def external(self) -> bool:
        return bool(self.raw.get("external"))

    @property
    def announcement(self) -> Announcement:
        return Announcement(self)

    async def refresh(self) -> "Chat":
        self.raw.update(await self.lark.request("GET", f"/open-apis/im/v1/chats/{self.id}",
                                                params={"user_id_type": "open_id"}))
        return self

    # ── messages ────────────────────────────────────────────────────────

    def messages(self, *, since: Any = None, until: Any = None, order: str = "asc",
                 limit: int | None = None) -> AsyncIterator[Message]:
        """History between ``since`` and ``until`` (datetimes, dates, unix
        times or ISO text), oldest first, or newest first with ``order="desc"``."""
        return _list_messages(self.lark, "chat", self.id, since=since, until=until, order=order, limit=limit)

    async def send(self, text: str | None = None, *, markdown: str | None = None, post: Mapping[str, Any] | None = None,
                   card: Mapping[str, Any] | None = None, image: Any = None, file: Any = None,
                   content: Any = None, msg_type: str | None = None, uuid: str | None = None) -> Message:
        """Sends one message: ``text``, ``markdown`` (rich text), a ``post``
        or ``card`` (dicts), an ``image`` or ``file`` (local path, or an
        uploaded key), or raw ``content`` with ``msg_type``. ``uuid`` makes a
        retried send idempotent."""
        return await self.lark.messages.send(self, text, markdown=markdown, post=post, card=card, image=image,
                                             file=file, content=content, msg_type=msg_type, uuid=uuid)

    # ── pins and the top notice ─────────────────────────────────────────

    async def pins(self, *, since: Any = None, until: Any = None, limit: int | None = None) -> AsyncIterator[Pin]:
        async for item in self.lark.pages("GET", "/open-apis/im/v1/pins",
                                          params={"chat_id": self.id, "start_time": to_seconds(since),
                                                  "end_time": to_seconds(until)}, page_size=50, limit=limit):
            yield Pin(self.lark, item)

    async def set_top_notice(self, message: "Message | str | None" = None) -> None:
        """Shows a message, or with no message the announcement, at the top of the chat."""
        notice = {"action_type": "2"} if message is None else {
            "action_type": "1", "message_id": message.id if isinstance(message, Message) else str(message)}
        await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/top_notice/put_top_notice",
                                json={"chat_top_notice": [notice]})

    async def clear_top_notice(self) -> None:
        await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/top_notice/delete_top_notice")

    # ── members and settings ────────────────────────────────────────────

    async def members(self, *, limit: int | None = None) -> AsyncIterator[User]:
        async for item in self.lark.pages("GET", f"/open-apis/im/v1/chats/{self.id}/members",
                                          params={"member_id_type": "open_id"}, page_size=100, limit=limit):
            yield User(self.lark, {"open_id": item.get("member_id"), "name": item.get("name"),
                                   "tenant_key": item.get("tenant_key")})

    async def add_members(self, users: Iterable[Any] = (), *, bots: Iterable[str] = ()) -> dict[str, Any]:
        """Adds people (Users or open ids) and bots (app ids); returns the ids that failed."""
        result: dict[str, Any] = {}
        user_ids = [user_open_id(user) for user in users]
        if user_ids:
            result.update(await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/members",
                                                  params={"member_id_type": "open_id", "succeed_type": 1},
                                                  json={"id_list": user_ids}))
        bot_ids = list(bots)
        if bot_ids:
            result.update(await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/members",
                                                  params={"member_id_type": "app_id", "succeed_type": 1},
                                                  json={"id_list": bot_ids}))
        return result

    async def remove_members(self, users: Iterable[Any] = (), *, bots: Iterable[str] = ()) -> dict[str, Any]:
        result: dict[str, Any] = {}
        user_ids = [user_open_id(user) for user in users]
        if user_ids:
            result.update(await self.lark.request("DELETE", f"/open-apis/im/v1/chats/{self.id}/members",
                                                  params={"member_id_type": "open_id"}, json={"id_list": user_ids}))
        bot_ids = list(bots)
        if bot_ids:
            result.update(await self.lark.request("DELETE", f"/open-apis/im/v1/chats/{self.id}/members",
                                                  params={"member_id_type": "app_id"}, json={"id_list": bot_ids}))
        return result

    async def add_managers(self, users: Iterable[Any]) -> None:
        await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/managers/add_managers",
                                params={"member_id_type": "open_id"},
                                json={"manager_ids": [user_open_id(user) for user in users]})

    async def remove_managers(self, users: Iterable[Any]) -> None:
        await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/managers/delete_managers",
                                params={"member_id_type": "open_id"},
                                json={"manager_ids": [user_open_id(user) for user in users]})

    async def update(self, *, name: str | None = None, description: str | None = None, owner: Any = None,
                     **settings: Any) -> "Chat":
        """Renames, describes or hands over the chat; ``settings`` passes other
        fields of the update API (``add_member_permission``, ``edit_permission``, …)."""
        body = {"name": name, "description": description,
                "owner_id": user_open_id(owner) if owner is not None else None, **settings}
        await self.lark.request("PUT", f"/open-apis/im/v1/chats/{self.id}", params={"user_id_type": "open_id"}, json=body)
        self.raw.update({key: value for key, value in body.items() if value is not None})
        return self

    async def share_link(self, validity: str = "week") -> str:
        """An invitation link valid for a ``week``, a ``year`` or ``permanently``."""
        data = await self.lark.request("POST", f"/open-apis/im/v1/chats/{self.id}/link",
                                       json={"validity_period": validity})
        return str(data.get("share_link") or "")

    async def disband(self) -> None:
        """Dissolves the group (owner, or the bot that created it)."""
        await self.lark.request("DELETE", f"/open-apis/im/v1/chats/{self.id}")


class Chats(Service):
    """Chats this identity belongs to."""

    def __aiter__(self) -> AsyncIterator[Chat]:
        return self.list().__aiter__()

    async def list(self, *, limit: int | None = None) -> AsyncIterator[Chat]:
        """Groups this identity is a member of."""
        async for item in self.lark.pages("GET", "/open-apis/im/v1/chats", params={"user_id_type": "open_id"},
                                          page_size=100, limit=limit):
            yield Chat(self.lark, item)

    async def search(self, query: str, *, limit: int | None = 50) -> AsyncIterator[Chat]:
        """Groups visible to this identity whose name or members match ``query``."""
        async for item in self.lark.pages("GET", "/open-apis/im/v1/chats/search",
                                          params={"query": query, "user_id_type": "open_id"}, page_size=100, limit=limit):
            yield Chat(self.lark, item)

    async def find(self, name: str) -> Chat:
        """The group named ``name`` (the closest match when none is exact)."""
        first = None
        async for chat in self.search(name, limit=50):
            if chat.name == name:
                return chat
            first = first or chat
        if first is None:
            raise LookupError(f"No chat named {name!r}")
        return first

    async def get(self, chat_id: str) -> Chat:
        return await Chat(self.lark, {"chat_id": chat_id}).refresh()

    def ref(self, chat_id: str) -> Chat:
        """A chat by id without a request."""
        return Chat(self.lark, {"chat_id": chat_id})

    async def with_user(self, user: Any) -> Chat:
        """The direct chat with a person (User, open id or email)."""
        if isinstance(user, str) and "@" in user:
            user = await self.lark.users.find(email=user)
        open_id = user_open_id(user)
        data = await self.lark.request("POST", "/open-apis/im/v1/chat_p2p/batch_query",
                                       params={"chatter_id_type": "open_id"}, json={"chatter_ids": [open_id]})
        for item in data.get("p2p_chats") or []:
            if item.get("chat_id"):
                return Chat(self.lark, {**item, "chat_mode": "p2p"})
        raise LookupError(f"No direct chat with {open_id}; send a message first with `user.send(...)`")

    async def create(self, name: str, *, users: Iterable[Any] = (), bots: Iterable[str] = (),
                     description: str | None = None, owner: Any = None, **settings: Any) -> Chat:
        """Creates a group with people and bots; ``settings`` passes other
        fields of the create API (``chat_mode``, ``external``, …)."""
        body = {"name": name, "description": description, "user_id_list": [user_open_id(user) for user in users] or None,
                "bot_id_list": list(bots) or None, "owner_id": user_open_id(owner) if owner is not None else None,
                **settings}
        data = await self.lark.request("POST", "/open-apis/im/v1/chats",
                                       params={"user_id_type": "open_id", "uuid": _uuid.uuid4().hex}, json=body)
        return Chat(self.lark, data)


class Messages(Service):
    async def get(self, message_id: str) -> Message:
        return await Message(self.lark, {"message_id": message_id}).refresh()

    async def get_many(self, message_ids: Iterable[str]) -> list[Message]:
        """Up to any number of messages, fetched 50 at a time."""
        ids = list(message_ids)
        result: list[Message] = []
        for start in range(0, len(ids), 50):
            data = await self.lark.request("GET", "/open-apis/im/v1/messages/mget",
                                           params={"message_ids": ids[start:start + 50],
                                                   "card_msg_content_type": "raw_card_content", **_WITH_NAMES})
            result.extend(Message(self.lark, item) for item in data.get("items") or [])
        return result

    def ref(self, message_id: str) -> Message:
        return Message(self.lark, {"message_id": message_id})

    async def search(self, query: str = "", *, chats: Iterable[Any] = (), senders: Iterable[Any] = (),
                     chat_type: str | None = None, since: Any = None, until: Any = None, at_me: bool = False,
                     limit: int | None = 100) -> AsyncIterator[Message]:
        """Messages across chats matching ``query`` and the filters
        (``chat_type`` ``group`` or ``p2p``), newest first."""
        filters: dict[str, Any] = {}
        chat_ids = [chat.id if isinstance(chat, Chat) else str(chat) for chat in chats]
        if chat_ids:
            filters["chat_ids"] = chat_ids
        sender_ids = [user_open_id(user) for user in senders]
        if sender_ids:
            filters["from_ids"] = sender_ids
        if chat_type:
            filters["chat_type"] = chat_type
        if since is not None or until is not None:
            filters["time_range"] = {"start_time": to_iso(since), "end_time": to_iso(until)}
        if at_me:
            filters["is_at_me"] = True
        body: dict[str, Any] = {"query": query}
        if filters:
            body["filter"] = filters
        batch: list[str] = []
        count = 0
        async for item in self.lark.pages("POST", "/open-apis/im/v1/messages/search", params={"user_id_type": "open_id"},
                                          json=body, page_size=50, limit=limit):
            message_id = (item.get("meta_data") or {}).get("message_id") or item.get("message_id")
            if message_id:
                batch.append(message_id)
            if len(batch) == 50:
                for message in await self.get_many(batch):
                    yield message
                    count += 1
                batch = []
        if batch:
            for message in await self.get_many(batch):
                yield message

    async def send(self, to: Any, text: str | None = None, *, uuid: str | None = None, **content: Any) -> Message:
        """Sends to a Chat, User, chat id (``oc_``), open id (``ou_``) or email;
        takes the content arguments of ``Chat.send``."""
        id_type, receive_id = receive_target(to)
        msg_type, body = await _content(self.lark, text, **{key: value for key, value in content.items() if value is not None})
        data = await self.lark.request("POST", "/open-apis/im/v1/messages", params={"receive_id_type": id_type},
                                       json={"receive_id": receive_id, "msg_type": msg_type, "content": body,
                                             "uuid": uuid or _uuid.uuid4().hex})
        return Message(self.lark, data)

    async def merge_forward(self, messages: Iterable[Any], to: Any) -> Message:
        """Forwards several messages of one chat as a single record (runs as the bot)."""
        id_type, receive_id = receive_target(to)
        ids = [message.id if isinstance(message, Message) else str(message) for message in messages]
        data = await self.lark.bot.request("POST", "/open-apis/im/v1/messages/merge_forward",
                                           params={"receive_id_type": id_type, "uuid": _uuid.uuid4().hex},
                                           json={"receive_id": receive_id, "message_id_list": ids})
        return Message(self.lark, data.get("message") or data)

    async def upload_image(self, path: Any) -> str:
        return await upload_image(self.lark, path)

    async def upload_file(self, path: Any) -> str:
        return await upload_file(self.lark, path)
