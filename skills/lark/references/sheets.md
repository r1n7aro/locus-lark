# Sheets

`lark.sheets` works with spreadsheets. A `Spreadsheet` is a cloud object; for comments, sharing, export and copying, see [drive.md](drive.md). Each `Worksheet` is one tab.

## Open and read

```python
book = await lark.sheets.open(link_or_token)          # sheets or wiki link; lark.open(link) also works
for ws in await book.worksheets():
    print(ws.id, ws.title, ws.row_count, ws.column_count, ws.hidden)
ws = await book.worksheet("明细")                     # title, sheet id or index; no argument: the link's ?sheet=, else the first
rows = await ws.read()                                # list of rows; the whole used grid by default
rows = await ws.read("A1:D20", render="formatted")    # value (raw), formatted, formula, text
records = await ws.records("A1:F")                    # dicts keyed by the header row (header_row=1)
values = await book.read_many([f"{ws.id}!A1:B5", f"{other.id}!C1:C9"])   # {range: rows}
```

Ranges are A1 references within the worksheet. A `<sheet_id>!` prefix addresses another tab.

## Write

```python
await ws.write("A1", [["名称", "数量"], ["苹果", 3]])  # from a start cell (the range is computed) or into "A1:B2"
await ws.append([["香蕉", 5]])                        # after the last row with data
await ws.clear("A2:D100")
await book.write_many({f"{ws.id}!A1:B1": [["x", "y"]], f"{other.id}!A1": [[1]]})
cells = await ws.find("苹果", cells="A:A", whole_cell=True)        # ["A2", ...]; match_case=, regex=
cells = await ws.replace("旧", "新", cells="B2:B100")
```

## Structure

```python
await ws.insert_rows(5, count=2)                      # 1-based row numbers; inserted before row 5
await ws.delete_rows(10, count=3)
await ws.insert_columns(2); await ws.delete_columns(4, 2)
await ws.merge("A1:C1")                               # how: MERGE_ALL, MERGE_ROWS, MERGE_COLUMNS
await ws.unmerge("A1:C1")
await ws.rename("新名字"); await ws.delete()
new_ws = await book.add_worksheet("汇总", index=0)
book = await lark.sheets.create("预算", folder=folder)
```
