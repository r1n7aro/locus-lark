# Chats, messages, users

`lark.chats`, `lark.messages`, `lark.users`. They act as the signed-in user, or as the bot when called through `lark.bot`.

## Find chats and people

```python
chat = await lark.chats.find("项目群")          # exact name, else the closest match; LookupError if none
async for chat in lark.chats.search("周报"):     # groups visible to this identity
    print(chat.id, chat.name, chat.is_direct)
async for chat in lark.chats.list(limit=100):    # chats this identity is in
    ...
chat = await lark.chats.get("oc_...")            # loaded; lark.chats.ref("oc_...") without a request
dm = await lark.chats.with_user("someone@corp.com")   # existing direct chat (User, open id or email)

me = await lark.users.me()
user = await lark.users.find(name="张三")        # or email=..., mobile=... (contact_lookup permission)
async for user in lark.users.search("zhang"):
    print(user.id, user.name, user.email)
```

`User` has `id` (open id), `name`, `email`, `avatar_url`, plus `await user.refresh()`, `await user.chat()` and `await user.send(...)`.

## Read history

```python
async for message in chat.messages(since="2026-11-01", until=None, order="asc", limit=500):
    print(message.created_at, message.sender.name, message.type, message.text)
async for reply in message.thread():              # replies in a thread
    ...
message = await lark.messages.get("om_...")
messages = await lark.messages.get_many(ids)
async for message in lark.messages.search("发布", chats=[chat], senders=[user], since=..., at_me=False,
                                          chat_type="group"):   # newest first
    ...
```

- `since` and `until` accept a datetime, a date, unix seconds or ISO text.
- `Message` properties:
  - `type`: `text`, `post`, `image`, `file`, `interactive`, and so on.
  - `text`: readable text for any type. `content` is the parsed JSON.
  - `mentions`, `sender` (a `User`), `sent_by_bot`, `chat`, `chat_id`.
  - `created_at`, `updated_at`, `edited`, `recalled`.
  - Threading: `thread_id`, `parent_id`, `root_id`.
- Files: `message.resource_keys()` lists `(key, kind)` pairs. `await message.download(path)` saves the first image or file; `path` can be a file or a folder, and `key=` picks another resource.
- Read receipts: `async for user in message.read_by()` lists who read a message this identity sent.

## Send

```python
await chat.send("plain text")
await chat.send(markdown="**bold** and [link](https://...)")        # rich text (post with Markdown)
await chat.send(image="chart.png")                                  # local path, or an image_key
await chat.send(file="report.pdf")
await chat.send(card={...})                                         # interactive card JSON
await chat.send(post={"title": "...", "content": [[{"tag": "text", "text": "..."}]]})
await chat.send(content={...}, msg_type="share_chat")               # any other type
await lark.messages.send("someone@corp.com", "hi")                  # Chat, User, oc_/ou_ id or email
await user.send(markdown="...")
reply = await message.reply("收到", in_thread=True)
```

- Each call takes exactly one content argument. `uuid=` makes a retried send idempotent.
- Sending as the user needs the optional `send_as_user` permission. On a `LarkScopeError`, run `await lark.login(optional=["send_as_user"])`. Alternatively, send through `lark.bot`, which needs the bot in the chat.
- `lark.messages.upload_image(path)` and `upload_file(path)` return keys you can reuse.

## Change messages

```python
await message.recall()
await message.edit("new text")                   # or markdown=..., post=... (runs as the bot; bot messages only)
await message.update_card({...})                 # cards the app sent
await message.forward(other_chat)                # Chat, User, id or email
await lark.messages.merge_forward([m1, m2], other_chat)   # one combined record (bot)
reaction = await message.react("THUMBSUP")       # emoji type, e.g. OK, SMILE, DONE
async for reaction in message.reactions(): ...   # .emoji, .operator_id; await reaction.remove()
pin = await message.pin(); await message.unpin()
async for pin in chat.pins(): (await pin.message()).text
failed = await message.urgent([user], via="app") # "sms" / "phone"; bot messages only
```

## Manage chats

```python
chat = await lark.chats.create("新项目", users=[user, "ou_..."], bots=["cli_..."], description="...")
await chat.add_members([user]); await chat.remove_members(["ou_..."])
async for member in chat.members(): print(member.id, member.name)
await chat.add_managers([user]); await chat.remove_managers([user])
await chat.update(name="改名", description="...", owner=user)   # other fields: add_member_permission=...
link = await chat.share_link("week")             # "year" / "permanently"
await chat.set_top_notice(message)               # pin a message at the top; no argument pins the announcement
await chat.clear_top_notice()
await chat.disband()                             # owner only; optional chat_admin permission
```

## Announcements

`chat.announcement` is the chat's announcement document. These calls need the optional `chat_admin` permission.

```python
text = await chat.announcement.text()
await chat.announcement.append("新的一段")
await chat.announcement.set("整段替换")          # clear() then append()
blocks = await chat.announcement.blocks()        # docs Block objects
```
