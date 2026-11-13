# Sign-in and permissions

## How the sign-in works

- The plugin's Settings tab (Plugins → 飞书 / Lark) handles the app setup.
  - "一键创建应用" creates a personal app in the user's tenant after they confirm in Feishu.
  - The user can also enter an existing app's App ID and App Secret.
- Signing in is a device flow. Locus opens a link, the user confirms in Feishu, and the refresh token goes to the system keychain. Sign-ins renew automatically for about a week of inactivity. After that, sign in again.
- Scripts get short-lived access tokens from the plugin's `lark_auth` tool. They never see the app secret or the refresh token.

```python
status = await locus.lark.status()
# AuthStatus(brand, app_ready, app_id, app_source, signed_in, user_name, open_id, scopes, expires_at, refresh_expires_at)
request = await locus.lark.login(domains=["im", "docs"], optional=["send_as_user"], scopes=["..."])
print(request.url)            # show it to the user; the browser opens too (open_browser=False to skip)
status = await request.wait() # until confirmed; LarkAuthError when declined or expired
await locus.lark.logout()
catalog = await locus.lark.auth.catalog()   # domains and optional groups with what is granted
```

## Permissions

- Without arguments, a sign-in asks for all default domains: `wiki`, `docs`, `drive`, `im`, `contact`, `sheets` and `base`.
- Sign-ins add up: permissions granted earlier are kept.
- Optional groups are not requested by default. In some tenants an admin must approve them.

| Optional group | Allows |
|---|---|
| `send_as_user` | Sending and replying to messages as the user |
| `chat_admin` | Disbanding chats, reading and writing chat announcements, chat pins |
| `contact_lookup` | `users.find(email=...)` / `mobile=...` |
| `wiki_rename` | `WikiNode.rename` |

Handling a missing permission:

```python
from locus.lark import LarkScopeError
try:
    await chat.send("hi")
except LarkScopeError as error:
    if error.app_side:
        print("The app must enable", error.scopes, "at", error.console_url)   # the user opens it, then retries
    else:
        request = await locus.lark.login(scopes=error.scopes)
        print(request.url); await request.wait()
```

## The bot identity

`locus.lark.bot` exposes the same services, acting as the app's bot with a tenant token.

- The bot sees only chats it is in, and documents shared with the app.
- Bot permissions are enabled on the app in the developer console. A `LarkScopeError` with `app_side=True` links there.
- Some calls always use the bot: `Message.edit`, `Message.urgent` and `messages.merge_forward`. Each one applies only to messages the bot sent.

## Raw API

Use the raw API for endpoints without a wrapper. The paths are those of the official API docs (open.feishu.cn/document).

```python
data = await locus.lark.api.get("/open-apis/calendar/v4/calendars", {"page_size": 50})
data = await locus.lark.api.post("/open-apis/im/v1/messages", {"receive_id": "oc_...", ...},
                                 params={"receive_id_type": "chat_id"})
async for item in locus.lark.api.pages("GET", "/open-apis/im/v1/chats", params={...}, items="items", limit=200):
    ...
blob = await locus.lark.api.get("/open-apis/drive/v1/medias/<token>/download")   # bytes for non-JSON replies
```

- Each call returns the reply's `data` and raises `LarkError` when `code` is not 0.
- Tokens, retries and errors are handled the same way as in the wrappers.
- Calls outside the default domains may need extra scopes. Pass them as `login(scopes=[...])`.
