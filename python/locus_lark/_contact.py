"""Users: who is signed in, look up people, and talk to them directly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ._core import LarkError, LarkScopeError, Resource, Service

if TYPE_CHECKING:
    from ._im import Chat, Message


class User(Resource):
    """A person (``id`` is the ``open_id``)."""

    _id_key = "open_id"

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or self.raw.get("en_name") or "")

    @property
    def email(self) -> str | None:
        return self.raw.get("email") or self.raw.get("enterprise_email")

    @property
    def avatar_url(self) -> str | None:
        avatar = self.raw.get("avatar")
        if isinstance(avatar, dict):
            return avatar.get("avatar_origin") or avatar.get("avatar_240")
        return self.raw.get("avatar_url") or avatar

    async def refresh(self) -> "User":
        data = await self.lark.request("GET", f"/open-apis/contact/v3/users/{self.id}", params={"user_id_type": "open_id"})
        self.raw.update(data.get("user") or {})
        return self

    async def chat(self) -> "Chat":
        """The direct conversation with this user."""
        return await self.lark.chats.with_user(self)

    async def send(self, text: str | None = None, **content: Any) -> "Message":
        """Sends a direct message; takes the arguments of ``Chat.send``."""
        return await self.lark.messages.send(self, text, **content)


class Users(Service):
    async def me(self) -> User:
        """The signed-in user (user identity only)."""
        data = await self.lark.request("GET", "/open-apis/authen/v1/user_info")
        return User(self.lark, data)

    async def get(self, open_id: str) -> User:
        data = await self.lark.request("GET", f"/open-apis/contact/v3/users/{open_id}", params={"user_id_type": "open_id"})
        return User(self.lark, data.get("user") or {"open_id": open_id})

    def ref(self, open_id: str, name: str | None = None) -> User:
        """A user by id without a request."""
        return User(self.lark, {"open_id": open_id, "name": name})

    async def search(self, query: str, *, limit: int | None = 20) -> AsyncIterator[User]:
        """People whose name, email or phone matches ``query``."""
        async for item in self.lark.pages("POST", "/open-apis/contact/v3/users/search", json={"query": query},
                                          page_size=20, limit=limit):
            meta = item.get("meta_data") or {}
            names = meta.get("i18n_names") or {}
            name = names.get("zh_cn") or names.get("en_us") or item.get("display_info") or ""
            yield User(self.lark, {"open_id": item.get("id"), "name": name, "email": meta.get("mail_address"), **meta})

    async def find(self, *, email: str | None = None, mobile: str | None = None, name: str | None = None) -> User:
        """One user by email, mobile number or (best match of) name."""
        if email or mobile:
            body = {"emails": [email] if email else None, "mobiles": [mobile] if mobile else None, "include_resigned": False}
            params = {"user_id_type": "open_id"}
            try:
                data = await self.lark.request("POST", "/open-apis/contact/v3/users/batch_get_id", params=params, json=body)
            except LarkScopeError:
                raise
            except LarkError:
                if self.lark.identity == "bot":
                    raise
                # The lookup may only accept the app's token; the open id is the same.
                data = await self.lark.bot.request("POST", "/open-apis/contact/v3/users/batch_get_id", params=params,
                                                   json=body)
            for item in data.get("user_list") or []:
                if item.get("user_id"):
                    return await self.get(item["user_id"])
            raise LookupError(f"No user with {'email ' + email if email else 'mobile ' + str(mobile)}")
        if name:
            first = None
            async for user in self.search(name, limit=20):
                if user.name == name:
                    return user
                first = first or user
            if first is not None:
                return first
            raise LookupError(f"No user named {name!r}")
        raise ValueError("pass email, mobile or name")
