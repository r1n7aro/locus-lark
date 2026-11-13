# Wiki

`lark.wiki` covers knowledge spaces (`WikiSpace`) and their pages (`WikiNode`). A page holds a document, sheet, base, mind note or file. `node.open()` returns that object without a request.

## Navigate

```python
async for space in lark.wiki.spaces():
    print(space.id, space.name)
space = await lark.wiki.space("产品知识库")           # by id or name
node = await lark.wiki.node("https://corp.feishu.cn/wiki/...")   # by link or node token
page = await lark.open(wiki_link)                    # directly the Document / Spreadsheet / Base

async for node in space.nodes():                     # top level; parent=node for a level below
    print(node.title, node.kind, node.has_children)
async for node in space.walk(depth=3): ...           # whole tree, depth first
async for child in node.children(): ...
async for page in node.walk(): ...
found = await space.find("发布流程")                 # first page with this title (LookupError if none)
parent = await node.parent()                         # None at the top
node = await lark.wiki.node_of(document)             # the page holding a Document / Spreadsheet / Base
```

`WikiNode` properties:

- `id`: the node token, as in the link.
- `title`, `kind` (`docx`, `sheet`, `bitable`, `mindnote`, `file`, ...), `obj_token`.
- `space` and `space_id`, `parent_token`, `has_children`, `is_shortcut`.

## Read and write page content

```python
doc = await node.document()                          # docx pages; else node.open()
text = await doc.markdown()
await doc.append("...")                              # see docs.md for all edits
```

## Create and reorganize

```python
page = await space.create_node("新页面", markdown="# 标题\n内容")         # top level; parent=node to nest
child = await node.create_child("子页面", kind="docx", markdown="...")   # kind: docx, sheet, bitable, mindnote
doc = await lark.docs.create("标题", markdown, wiki=node)                 # same, returns the Document
await node.rename("新标题")                          # optional wiki_rename permission
await node.move(parent=other_node)                   # or space=other_space
copy = await node.copy(title="副本", parent=other_node)
shortcut = await node.add_shortcut(parent=other_node)
await space.move_in(drive_document, parent=node)     # move a Drive Document / Spreadsheet / Base / file into the space
space = await lark.wiki.create_space("团队空间", description="...")
```

Comments, sharing and export of the page's content go through the opened object (see [drive.md](drive.md)).

## Space members

```python
async for member in space.members(): print(member)   # raw dicts: member_type, member_id, member_role
await space.add_member(user, role="member")          # or "admin"; User, Chat, open id, chat id, email, department id
await space.remove_member(user, role="member")
```
