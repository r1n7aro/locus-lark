# Base (多维表格)

`lark.base` works with Bitable apps. A `Base` is a cloud object; for sharing, export and copying, see [drive.md](drive.md).

## Open

```python
base = await lark.base.open(link_or_token)           # base or wiki link (?table= and ?view= are kept); lark.open(link)
for table in await base.tables(): print(table.id, table.name)
table = await base.table("任务")                     # name, table id or index; no argument: the link's ?table=, else the first
base = await lark.base.create("项目跟踪", folder=folder)
table = await base.create_table("缺陷", ["标题", {"field_name": "严重度", "type": 3}])
```

## Fields and views

```python
for field in await table.fields():
    print(field.id, field.name, field.type, field.options, field.is_primary)
await table.add_field("状态", "single_select", options=[{"name": "待办"}, {"name": "完成"}])
await table.update_field("状态", name="进度")
await table.delete_field("旧字段")
for view in await table.views(): print(view.id, view.name, view.type)
```

Field type names: `text`, `number`, `single_select`, `multi_select`, `date`, `checkbox`, `user`, `phone`, `url`, `attachment`, `link`, `lookup`, `formula`, `duplex_link`, `location`, `group`, `created_time`, `modified_time`, `created_by`, `modified_by`, `auto_number`. Numbers work too.

## Records

```python
async for record in table.records(where={"状态": "完成", "分数": (">=", 80)}, sort=["-分数"], limit=200):
    print(record.id, record["标题"], record.fields)
record = await table.first(标题="登录失败")          # equality on fields; None if nothing matches
record = await table.get("rec...")
record["状态"] = "完成"; await record.save()         # only changed fields are sent
await record.refresh(); await record.delete()

new = await table.add({"标题": "新任务", "分数": 90})  # or table.add(标题="新任务")
rows = await table.add_many([{"标题": "a"}, {"标题": "b"}])            # batches of 500
await table.update_many([record_a, ("rec...", {"状态": "待办"})])
await table.delete_many([record_a, "rec..."])
```

- `where` maps field names to a value (equality) or a tuple `(operator, value...)`. Operators: `==`, `!=`, `contains`, `not_contains`, `empty`, `not_empty`, `>`, `>=`, `<`, `<=`. All conditions must match.
- A dict with `conjunction` and `conditions` is passed to the search API unchanged.
- Other `records` options: `view=` (a View or view id) and `fields=` (the field names to return).
- Value shapes follow the Bitable API:
  - Select fields take option names.
  - User fields take `[{"id": "ou_..."}]`.
  - Dates are milliseconds.
  - Link fields take record ids.
