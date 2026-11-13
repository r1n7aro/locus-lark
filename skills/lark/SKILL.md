---
summary: >-
  Use when a task reads or writes Feishu / Lark (飞书) content with locus.lark in Python: wiki, docs, sheets, bases, drive files, comments, sharing, chats, messages. Ignore other chat or document services.
tools:
  - python
---

# 飞书 / Lark

`locus.lark` is an object-oriented async client in the python tool. It comes from the `lark` plugin and loads on first use. Requests go directly to the Feishu / Lark Open Platform as the signed-in user. The plugin keeps the app secret and the refresh token; scripts only receive short-lived tokens.

## Start

```python
lark = locus.lark
status = await lark.status()                 # AuthStatus: app_ready, signed_in, user_name, scopes
if not status.signed_in:
    request = await lark.login()             # opens the browser; also show request.url to the user
    print(request.url, request.user_code)
    status = await request.wait()
me = await lark.me()
```

- If no app is set up (`app_ready` is false), ask the user to open Plugins → 飞书 / Lark → Settings. There they can create an app in one step or enter an existing one. Do not ask for secrets in the chat.
- `lark.open(link)` returns the object behind any Feishu link: `Document`, `Spreadsheet`, `Base`, `Folder`, `DriveFile`, or the page behind a wiki link (`WikiNode` for other wiki pages).

## Model

- Services are attributes of the client: `lark.chats`, `lark.messages`, `lark.users`, `lark.wiki`, `lark.docs`, `lark.drive`, `lark.sheets` and `lark.base`.
  - `lark.auth` manages the sign-in.
  - `lark.api` makes raw calls to any endpoint: `await lark.api.get("/open-apis/...", params)`, plus `post`, `put`, `patch`, `delete` and `pages(method, path, items="items")`.
- Objects are classes with properties and methods: `Chat`, `Message`, `User`, `Document`, `Block`, `WikiSpace`, `WikiNode`, `Folder`, `DriveFile`, `Spreadsheet`, `Worksheet`, `Base`, `Table`, `Record`, `Comment`, `Member`.
  - `obj.id` is the object's id and `obj.raw` is the platform's JSON.
  - Objects keep the identity they came from.
- `lark.bot` is the same client acting as the app's bot. Some calls always run as the bot: editing messages, urgent notifications and merge-forwarding. The bot only sees chats it belongs to.
- Lists are async iterators. They fetch pages lazily, and most accept `limit`: `async for chat in lark.chats.list(limit=50)`.
- Every method is `async`, except `ref(...)` constructors (objects by id, without a request) and plain properties.

## Errors

- `locus.lark.LarkError` has `code`, `log_id`, `status` and the raw reply in `data`.
- `LarkAuthError`: the user is not signed in, or the sign-in expired. Run the Start steps.
- `LarkScopeError`: a permission is missing (`error.scopes`).
  - `app_side` false: run `await lark.login(scopes=error.scopes)` (or `optional=[...]`) and show the link to the user.
  - `app_side` true: the app must enable the permissions first. Give the user `error.console_url`.
- Rate limits and expired tokens are retried automatically.

## Reference

Read the reference for the area the task touches:

- [Chats, messages, users](references/messages.md): history, search, send and reply, files, reactions, pins, announcements, members, creating and disbanding chats, urgent notifications, forwarding.
- [Documents](references/docs.md): Markdown read and write, block edits, images and files in documents, history versions, document search.
- [Wiki](references/wiki.md): spaces, page trees, creating, moving and copying pages, space members.
- [Drive, comments, sharing](references/drive.md): folders, upload and download, import and export, comments, collaborators, link sharing, copying, moving and deleting. These apply to documents, sheets and bases alike.
- [Sheets](references/sheets.md): ranges, records, writing, appending, find and replace, rows and columns, merging.
- [Base (多维表格)](references/base.md): tables, fields, views, record queries and edits.
- [Sign-in and permissions](references/auth.md): permission groups, the bot identity, raw API calls and paging.
