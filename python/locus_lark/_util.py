"""Shared helpers: times, links, ids and message content."""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

def to_seconds(value: Any) -> str | None:
    """Unix seconds as a string, from a datetime, date, number or ISO text."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.astimezone()
        return str(int(value.timestamp()))
    if isinstance(value, _dt.date):
        return str(int(_dt.datetime(value.year, value.month, value.day).astimezone().timestamp()))
    if isinstance(value, (int, float)):
        # Milliseconds are accepted too.
        return str(int(value / 1000 if value > 10**11 else value))
    text = str(value).strip()
    if text.isdigit():
        return to_seconds(int(text))
    parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return to_seconds(parsed)


def to_millis(value: Any) -> str | None:
    seconds = to_seconds(value)
    return None if seconds is None else str(int(seconds) * 1000)


def from_timestamp(value: Any) -> _dt.datetime | None:
    """A local datetime from seconds or milliseconds (string or number) or ISO 8601 text."""
    if value in (None, "", "0", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        try:
            parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.astimezone()).astimezone()
    if number > 10**11:
        number /= 1000
    return _dt.datetime.fromtimestamp(number).astimezone()


# ── ids ─────────────────────────────────────────────────────────────────


def user_id_type(value: str) -> str:
    """The receive/member id type of a user id or email."""
    if "@" in value:
        return "email"
    if value.startswith("ou_"):
        return "open_id"
    if value.startswith("on_"):
        return "union_id"
    return "user_id"


def receive_target(target: Any) -> tuple[str, str]:
    """``(receive_id_type, receive_id)`` for a Chat, User, chat id, user id or email."""
    from ._contact import User
    from ._im import Chat

    if isinstance(target, Chat):
        return "chat_id", target.id
    if isinstance(target, User):
        return "open_id", target.id
    text = str(target).strip()
    if text.startswith("oc_"):
        return "chat_id", text
    return user_id_type(text), text


def user_open_id(user: Any) -> str:
    from ._contact import User

    if isinstance(user, User):
        return user.id
    return str(user).strip()


# ── links ───────────────────────────────────────────────────────────────

_KINDS = {
    "docx": "docx", "docs": "doc", "doc": "doc", "wiki": "wiki", "sheets": "sheet", "sheet": "sheet",
    "base": "bitable", "bitable": "bitable", "file": "file", "folder": "folder", "mindnotes": "mindnote",
    "slides": "slides",
}


@dataclass(frozen=True)
class Link:
    """A parsed Feishu / Lark link: ``kind`` (docx, wiki, sheet, bitable,
    file, folder, …), ``token`` and the query (``sheet``, ``table``, ``view``)."""

    kind: str
    token: str
    query: dict[str, str]


def parse_link(value: str) -> Link | None:
    text = str(value).strip()
    if not text.startswith("http"):
        return None
    parsed = urlparse(text)
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] == "drive" and len(parts) >= 3 and parts[1] == "folder":
        parts = parts[1:]
    for index, part in enumerate(parts[:-1]):
        kind = _KINDS.get(part)
        if kind:
            query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            return Link(kind, parts[index + 1], query)
    return None


def token_of(value: Any, kinds: tuple[str, ...] = ()) -> tuple[str, str | None, dict[str, str]]:
    """``(token, kind, query)`` from a link, a token or an object with ``token``."""
    token = getattr(value, "token", None)
    if token:
        return str(token), getattr(value, "doc_type", None), {}
    link = parse_link(str(value))
    if link is not None:
        if kinds and link.kind not in kinds and link.kind != "wiki":
            raise ValueError(f"{value} is a {link.kind} link, not {' or '.join(kinds)}")
        return link.token, link.kind, link.query
    return str(value).strip(), None, {}


# ── message content ─────────────────────────────────────────────────────


def message_content(
    text: str | None = None,
    *,
    markdown: str | None = None,
    post: Mapping[str, Any] | None = None,
    card: Mapping[str, Any] | None = None,
    image_key: str | None = None,
    file_key: str | None = None,
    content: Any = None,
    msg_type: str | None = None,
) -> tuple[str, str]:
    """``(msg_type, content JSON)`` for one of the ways to say something."""
    given = [name for name, value in (("text", text), ("markdown", markdown), ("post", post), ("card", card),
             ("image", image_key), ("file", file_key), ("content", content)) if value is not None]
    if len(given) != 1:
        raise ValueError("pass exactly one of text, markdown, post, card, image, file or content")
    if text is not None:
        return "text", json.dumps({"text": text}, ensure_ascii=False)
    if markdown is not None:
        return "post", json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": markdown}]]}}, ensure_ascii=False)
    if post is not None:
        body = post if any(key in post for key in ("zh_cn", "en_us", "ja_jp")) else {"zh_cn": post}
        return "post", json.dumps(body, ensure_ascii=False)
    if card is not None:
        return "interactive", json.dumps(card, ensure_ascii=False)
    if image_key is not None:
        return "image", json.dumps({"image_key": image_key})
    if file_key is not None:
        return "file", json.dumps({"file_key": file_key})
    if msg_type is None:
        raise ValueError("content needs msg_type")
    return msg_type, content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def plain_text(msg_type: str, content: Mapping[str, Any]) -> str:
    """Readable text of a message's content."""
    if msg_type == "text":
        return str(content.get("text", ""))
    if msg_type == "post":
        body = content
        if "content" not in body:
            body = next((value for value in content.values() if isinstance(value, Mapping)), {})
        lines = []
        if body.get("title"):
            lines.append(str(body["title"]))
        for paragraph in body.get("content") or []:
            pieces = []
            for element in paragraph or []:
                tag = element.get("tag")
                if tag in ("text", "md", "code_block"):
                    pieces.append(str(element.get("text", "")))
                elif tag == "a":
                    pieces.append(str(element.get("text") or element.get("href", "")))
                elif tag == "at":
                    pieces.append("@" + str(element.get("user_name") or element.get("user_id", "")))
                elif tag == "emotion":
                    pieces.append(f"[{element.get('emoji_type', '')}]")
                elif tag in ("img", "media"):
                    pieces.append("[image]" if tag == "img" else "[media]")
            lines.append("".join(pieces))
        return "\n".join(lines).strip()
    if msg_type == "interactive":
        title = (content.get("header") or {}).get("title") or {}
        return str(title.get("content") or content.get("title") or "[card]")
    if msg_type in ("image", "file", "audio", "media", "sticker"):
        return f"[{msg_type}]" if msg_type != "file" else f"[file {content.get('file_name', '')}]".rstrip()
    if msg_type == "share_chat":
        return f"[chat {content.get('chat_id', '')}]"
    if msg_type == "share_user":
        return f"[user {content.get('user_id', '')}]"
    if msg_type == "system":
        return str(content.get("template") or "[system]")
    return str(content.get("text") or f"[{msg_type}]")


# ── files ───────────────────────────────────────────────────────────────


def read_file(path: Any) -> tuple[str, bytes, str]:
    """``(name, bytes, mime type)`` of a local file."""
    file = Path(path).expanduser()
    data = file.read_bytes()
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    return file.name, data, mime


def write_file(path: Any, data: bytes) -> Path:
    file = Path(path).expanduser()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    return file


_SAFE = re.compile(r'[\\/:*?"<>|\r\n]+')


def safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip() or "file"


def text_elements(text: str) -> list[dict[str, Any]]:
    """Comment reply elements for plain text."""
    return [{"type": "text_run", "text_run": {"text": text}}]


def elements_text(elements: list[Mapping[str, Any]] | None) -> str:
    pieces = []
    for element in elements or []:
        kind = element.get("type")
        if kind == "text_run":
            pieces.append(str((element.get("text_run") or {}).get("text", "")))
        elif kind == "docs_link":
            pieces.append(str((element.get("docs_link") or {}).get("url", "")))
        elif kind == "person":
            pieces.append("@" + str((element.get("person") or {}).get("user_id", "")))
    return "".join(pieces)
