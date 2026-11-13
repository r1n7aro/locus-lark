# Documents

`lark.docs` handles new-style documents (docx), opened by a docx link, a wiki link or a `document_id`. A `Document` is also a cloud object. For comments, sharing, export, copy, move and delete, see [drive.md](drive.md).

## Read

```python
doc = await lark.docs.open("https://corp.feishu.cn/wiki/...")   # or lark.open(link)
print(doc.title, doc.url)
text = await doc.markdown()                    # whole document as Markdown
xml = await doc.xml()                          # DocxXML; every element has the block id edits take
plain = await doc.raw_text()
for block in await doc.blocks():               # top-level blocks with nested children
    print(block.id, block.type, block.text)    # type: text, heading1..9, bullet, ordered, code, quote, todo, image, table, ...
for block in await doc.all_blocks(): ...       # flat, in document order
```

`doc.markdown(revision=n)` reads an older revision.

## Write

Prefer Markdown for new content.

```python
doc = await lark.docs.create("周报", "# 本周\n- ...")                # Drive root; folder=Folder or link
doc = await lark.docs.create("设计说明", markdown, wiki=space)       # page in a WikiSpace or under a WikiNode
await doc.append("## 新章节\n内容")
await doc.overwrite(markdown)                                        # replace everything
await doc.replace("旧词", "新词")                                    # inline text within paragraphs
await doc.insert_after(block, "插入的段落")                          # Block or block id; "0" start, "-1" end
await doc.replace_blocks(block, "替换后的内容", until=last_block)    # one block or a sibling range
await doc.delete_blocks(block, until=last_block)                     # or a list of blocks
await doc.move_blocks([b1, b2], after=anchor)
await doc.copy_blocks([b1], after=anchor)
```

- Every edit accepts `format="xml"` for DocxXML content. This keeps rich structure such as callouts, tables and colored text exactly.
- Find target blocks with `doc.blocks()` (by `type` and `text`), or read their ids from `doc.xml()`.
- Editing a single block through the block API: `await block.set_text("...")`, `await block.add_children([text_block("段落"), text_block("要点", "bullet")])`, `await block.delete()`. `text_block` is `locus.lark.text_block`.

## Images and files

```python
image = await doc.insert_image("chart.png", after=block, width=640, align="center", caption="图 1")
file_block = await doc.insert_file("data.xlsx")                     # at the end by default
```

Both upload the file and fill a new block. `index=` places the block among the top-level blocks instead of using `after=`.

## History

```python
for version in await doc.history(limit=20):    # newest first
    print(version.id, version.revision_id, version.created_at, version.name, version.editor_ids)
await doc.revert(version)                      # waits; LarkError if it fails (error.data["failed_block_tokens"])
```

## Search

```python
async for result in lark.docs.search("季度规划", kinds=["docx", "sheet"]):
    print(result.title, result.kind, result.url, result.updated_at)
    obj = await result.open()                  # the Document, Spreadsheet, Base, ...
```
