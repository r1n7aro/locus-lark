# 飞书 / Lark for Locus

Connects Locus to Feishu / Lark. Sign in once on the plugin's Settings tab. Then scripts in the Locus python tool can read and write Feishu content through `locus.lark`, acting as you or as the app's bot.

```python
lark = locus.lark
chat = await lark.chats.find("项目群")
async for message in chat.messages(since="2026-11-01"):
    print(message.sender.name, message.text)
doc = await lark.open("https://example.feishu.cn/wiki/...")
await doc.append("## 结论\n- ...")
```

## What it covers

| Area | Coverage |
|---|---|
| Wiki | Spaces, page trees, create, rename, move, copy and shortcut pages, space members |
| Documents | Markdown / DocxXML read and write, block edits, images and files, history and restore, search |
| Drive | Folders, upload and download, import (Word, Markdown, Excel, CSV), export (docx, pdf, md, xlsx, csv), copy, move, delete |
| Comments and sharing | Comments and replies, collaborators, owner transfer, link sharing, for documents, sheets, bases and files |
| Sheets | Ranges, records, write, append, clear, find and replace, rows and columns, merge, tabs |
| Base | Tables, fields, views, record queries with filters and sorting, batch create, update and delete |
| Chats and messages | History, search, send and reply (text, Markdown, images, files, cards), recall, edit, forward, merge-forward, reactions, pins, top notices, urgent notifications, announcements, members, managers, create and disband chats |
| Anything else | `lark.api` calls any Open Platform endpoint with the same sign-in |

## Setup

1. Install the plugin and open Plugins → 飞书 / Lark → Settings.
2. Set up an app:
   - Click **一键创建应用** to create a personal app in your tenant (you confirm in Feishu), or
   - enter an existing app's App ID and App Secret.
3. Click **登录** and confirm in Feishu.

Optional permissions, such as sending messages as yourself or managing chat announcements, are requested separately. In some tenants they need an admin's approval.

## How it works

- **Sign-in.** The `lark_auth` tool handles it. It is the only part that holds the app secret and the refresh token, both kept in the system keychain. Scripts receive short-lived access tokens and call the Open Platform directly over one keep-alive connection.
- **No standing cost.** Installing the plugin adds no rule and no tool that is always loaded:
  - Its single tool loads lazily.
  - Its Skill contributes one summary line.
  - `locus.lark` and its submodules import on first use. `httpx` is installed with the selected Python the first time it is needed.
- **Needs** Locus 0.9.4 or later.

## License

MIT. The sign-in protocols follow the official Feishu / Lark CLI ([larksuite/cli](https://github.com/larksuite/cli), MIT) and Python SDK ([lark-oapi](https://github.com/larksuite/oapi-sdk-python), MIT); see [LICENSE](LICENSE).
