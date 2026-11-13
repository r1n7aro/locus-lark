# Drive, comments, sharing

## Folders and files

```python
root = await lark.drive.root()
async for item in root.files():                      # typed: Document, Spreadsheet, Base, Folder or DriveFile
    print(item.name, item.doc_type, item.token)      # doc_type: docx, sheet, bitable, folder, file, ...
folder = lark.drive.folder("fldcn...")               # by token; await lark.open(folder_link) also works
sub = await folder.create_folder("2026")
uploaded = await folder.upload("report.pdf")         # or lark.drive.upload(path, folder=..., name=...)
await uploaded.download("downloads/")                # a file or a folder path
await lark.drive.download("boxcn...", "out.bin")
async for result in lark.drive.search("合同"): ...   # same as lark.docs.search
```

Uploads larger than 20 MB are sent in parts automatically.

## Import and export

```python
doc = await lark.drive.import_file("notes.md")                       # Word/Markdown/text/HTML → Document
sheet = await lark.drive.import_file("data.xlsx", folder=folder)     # Excel/CSV → Spreadsheet
base = await lark.drive.import_file("rows.csv", kind="bitable")      # → Base
path = await doc.export("exports/", "docx")          # docx / pdf / md for documents
path = await sheet.export("exports/", "xlsx")        # xlsx / csv; csv needs sub_id=<sheet id> (or table id for bases)
```

## Shared operations of cloud objects

`Document`, `Spreadsheet`, `Base`, `DriveFile` and `Folder` share these operations.

```python
meta = await obj.meta()                              # title, owner, times, url
copy = await obj.copy("副本", folder=folder)
await obj.move(folder)
await obj.delete()
```

### Comments

```python
async for comment in doc.comments(solved=False):     # whole=True: only whole-document comments
    print(comment.id, comment.text, comment.quote, comment.solved, comment.author_id)
    for reply in comment.replies: print(reply.text)
    async for reply in comment.all_replies(): ...    # beyond the first page of replies
c = await doc.comment("整体意见")                     # a whole-document comment
r = await c.reply("已修改"); await r.edit("已修改完"); await r.delete()
await c.resolve(); await c.reopen()
```

### Collaborators

```python
for member in await doc.members():
    print(member.kind, member.id, member.perm, member.name)   # perm: view, edit, full_access
await doc.share(user, "edit", notify=True)           # User, Chat, open id, chat id, email or department id
await doc.share("someone@corp.com", "view")          # an existing collaborator is updated
await member.set_perm("view"); await member.remove()
await doc.unshare(user)
await doc.transfer_owner(user, keep_access=True)
```

### Link sharing

```python
settings = await doc.sharing()                       # the public permission settings
await doc.set_sharing(link="tenant_readable")        # closed, tenant_readable, tenant_editable, anyone_readable, anyone_editable
await doc.set_sharing(external=False)                # other fields of the public permission API pass through
```
