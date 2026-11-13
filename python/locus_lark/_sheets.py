"""Spreadsheets: worksheets, reading and writing ranges, rows, find and replace.

Ranges use A1 notation inside a worksheet (``"A1:C10"``, ``"B:B"``); a
worksheet prefixes its own id. Values are lists of rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ._cloud import CloudObject
from ._core import Resource, Service
from ._util import token_of

_RENDER = {"value": "UnformattedValue", "formatted": "FormattedValue", "formula": "Formula", "text": "ToString"}


def _cell(ref: str) -> tuple[int, int]:
    """``(column, row)`` numbers (1-based) of an A1 reference."""
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    digits = "".join(ch for ch in ref if ch.isdigit())
    column = 0
    for ch in letters:
        column = column * 26 + ord(ch) - 64
    return max(1, column), int(digits or 1)


def column_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


class Worksheet(Resource):
    """One sheet of a spreadsheet (``id`` is the ``sheet_id``)."""

    _id_key = "sheet_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], spreadsheet: "Spreadsheet") -> None:
        super().__init__(lark, raw)
        self.spreadsheet = spreadsheet

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    @property
    def index(self) -> int:
        return int(self.raw.get("index") or 0)

    @property
    def row_count(self) -> int:
        return int((self.raw.get("grid_properties") or {}).get("row_count") or 0)

    @property
    def column_count(self) -> int:
        return int((self.raw.get("grid_properties") or {}).get("column_count") or 0)

    @property
    def hidden(self) -> bool:
        return bool(self.raw.get("hidden"))

    def _range(self, cells: str | None) -> str:
        if not cells:
            last = column_letter(max(1, self.column_count))
            return f"{self.id}!A1:{last}{max(1, self.row_count)}"
        return cells if "!" in cells else f"{self.id}!{cells}"

    @property
    def _base(self) -> str:
        return f"/open-apis/sheets/v2/spreadsheets/{self.spreadsheet.token}"

    async def read(self, cells: str | None = None, *, render: str = "value") -> list[list[Any]]:
        """Values of a range (the whole used grid by default). ``render`` is
        ``value`` (raw), ``formatted``, ``formula`` or ``text``."""
        data = await self.lark.request("GET", f"{self._base}/values/{self._range(cells)}",
                                       params={"valueRenderOption": _RENDER.get(render, render),
                                               "dateTimeRenderOption": "FormattedString"})
        return list((data.get("valueRange") or {}).get("values") or [])

    async def records(self, cells: str | None = None, *, header_row: int = 1) -> list[dict[str, Any]]:
        """Rows as dicts keyed by the header row."""
        rows = await self.read(cells)
        if len(rows) < header_row:
            return []
        header = [str(name) if name is not None else f"col{index + 1}" for index, name in enumerate(rows[header_row - 1])]
        return [dict(zip(header, row)) for row in rows[header_row:] if any(cell not in (None, "") for cell in row)]

    async def write(self, cells: str, values: Sequence[Sequence[Any]]) -> dict[str, Any]:
        """Writes rows starting at a cell (``"A1"``) or into a range."""
        values = [list(row) for row in values]
        prefix, _, local = cells.rpartition("!")
        if ":" not in local and values:
            column, row = _cell(local)
            width = max(len(item) for item in values)
            local = f"{local}:{column_letter(column + width - 1)}{row + len(values) - 1}"
            cells = f"{prefix}!{local}" if prefix else local
        return await self.lark.request("PUT", f"{self._base}/values",
                                       json={"valueRange": {"range": self._range(cells), "values": values}})

    async def append(self, values: Sequence[Sequence[Any]], cells: str | None = None) -> dict[str, Any]:
        """Adds rows after the last row with data (within ``cells`` columns, all by default)."""
        target = self._range(cells or f"A1:{column_letter(max(1, self.column_count, *(len(row) for row in values)))}")
        return await self.lark.request("POST", f"{self._base}/values_append", params={"insertDataOption": "INSERT_ROWS"},
                                       json={"valueRange": {"range": target, "values": [list(row) for row in values]}})

    async def clear(self, cells: str) -> None:
        """Empties a range such as ``"A2:D100"``."""
        prefix, _, local = cells.rpartition("!")
        first, _, last = local.partition(":")
        start_column, start_row = _cell(first)
        end_column, end_row = _cell(last) if last else (start_column, start_row)
        width, height = end_column - start_column + 1, end_row - start_row + 1
        if width <= 0 or height <= 0:
            raise ValueError(f"Not a range: {cells!r}")
        await self.write(cells, [[""] * width for _ in range(height)])

    async def find(self, text: str, *, cells: str | None = None, match_case: bool = False,
                   whole_cell: bool = False, regex: bool = False) -> list[str]:
        """Cells (A1 references) containing ``text``."""
        data = await self.lark.request(
            "POST", f"/open-apis/sheets/v3/spreadsheets/{self.spreadsheet.token}/sheets/{self.id}/find",
            json={"find_condition": {"range": self._range(cells), "match_case": match_case,
                                     "match_entire_cell": whole_cell, "search_by_regex": regex},
                  "find": text})
        return list((data.get("find_result") or {}).get("matched_cells") or [])

    async def replace(self, text: str, replacement: str, *, cells: str | None = None, match_case: bool = False,
                      whole_cell: bool = False, regex: bool = False) -> list[str]:
        """Replaces ``text``; returns the changed cells."""
        data = await self.lark.request(
            "POST", f"/open-apis/sheets/v3/spreadsheets/{self.spreadsheet.token}/sheets/{self.id}/replace",
            json={"find_condition": {"range": self._range(cells), "match_case": match_case,
                                     "match_entire_cell": whole_cell, "search_by_regex": regex},
                  "find": text, "replacement": replacement})
        return list((data.get("replace_result") or {}).get("matched_cells") or [])

    async def insert_rows(self, start: int, count: int = 1) -> None:
        """Inserts empty rows before row ``start`` (1-based)."""
        await self._dimension("insert_dimension_range", "ROWS", start - 1, start - 1 + count)

    async def insert_columns(self, start: int, count: int = 1) -> None:
        await self._dimension("insert_dimension_range", "COLUMNS", start - 1, start - 1 + count)

    async def delete_rows(self, start: int, count: int = 1) -> None:
        await self.lark.request("DELETE", f"{self._base}/dimension_range", json={
            "dimension": {"sheetId": self.id, "majorDimension": "ROWS", "startIndex": start, "endIndex": start + count - 1}})

    async def delete_columns(self, start: int, count: int = 1) -> None:
        await self.lark.request("DELETE", f"{self._base}/dimension_range", json={
            "dimension": {"sheetId": self.id, "majorDimension": "COLUMNS", "startIndex": start, "endIndex": start + count - 1}})

    async def _dimension(self, action: str, major: str, start: int, end: int) -> None:
        await self.lark.request("POST", f"{self._base}/{action}", json={
            "dimension": {"sheetId": self.id, "majorDimension": major, "startIndex": start, "endIndex": end},
            "inheritStyle": "BEFORE" if start > 0 else "AFTER"})

    async def merge(self, cells: str, how: str = "MERGE_ALL") -> None:
        """Merges a range (``MERGE_ALL``, ``MERGE_ROWS`` or ``MERGE_COLUMNS``)."""
        await self.lark.request("POST", f"{self._base}/merge_cells", json={"range": self._range(cells), "mergeType": how})

    async def unmerge(self, cells: str) -> None:
        await self.lark.request("POST", f"{self._base}/unmerge_cells", json={"range": self._range(cells)})

    async def rename(self, title: str) -> None:
        await self.spreadsheet._batch([{"updateSheet": {"properties": {"sheetId": self.id, "title": title}}}])
        self.raw["title"] = title

    async def delete(self) -> None:
        await self.spreadsheet._batch([{"deleteSheet": {"sheetId": self.id}}])


class Spreadsheet(CloudObject):
    """A spreadsheet (``id`` is the ``spreadsheet_token``). Also offers
    comments, collaborators, sharing and export (``xlsx``/``csv``)."""

    _id_key = "spreadsheet_token"
    doc_type = "sheet"

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    async def refresh(self) -> "Spreadsheet":
        data = await self.lark.request("GET", f"/open-apis/sheets/v3/spreadsheets/{self.token}")
        self.raw.update(data.get("spreadsheet") or {})
        return self

    async def worksheets(self) -> list[Worksheet]:
        data = await self.lark.request("GET", f"/open-apis/sheets/v3/spreadsheets/{self.token}/sheets/query")
        return [Worksheet(self.lark, item, self) for item in data.get("sheets") or []]

    async def worksheet(self, which: str | int = 0) -> Worksheet:
        """A worksheet by title, id or position (``0`` is the first; a link's ``?sheet=`` wins)."""
        sheets = await self.worksheets()
        if which == 0 and self.raw.get("_sheet"):
            which = self.raw["_sheet"]
        if isinstance(which, int):
            return sorted(sheets, key=lambda sheet: sheet.index)[which]
        for sheet in sheets:
            if which in (sheet.title, sheet.id):
                return sheet
        raise LookupError(f"No worksheet {which!r} in {self.title or self.token}")

    async def _batch(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.lark.request("POST", f"/open-apis/sheets/v2/spreadsheets/{self.token}/sheets_batch_update",
                                       json={"requests": requests})

    async def add_worksheet(self, title: str, *, index: int | None = None) -> Worksheet:
        data = await self._batch([{"addSheet": {"properties": {"title": title, "index": index}}}])
        reply = next(iter(data.get("replies") or []), {})
        properties = (reply.get("addSheet") or {}).get("properties") or {}
        return Worksheet(self.lark, {"sheet_id": properties.get("sheetId"), "title": properties.get("title", title),
                                     "index": properties.get("index", index or 0)}, self)

    async def read_many(self, ranges: Iterable[str], *, render: str = "value") -> dict[str, list[list[Any]]]:
        """Several ranges (``"<sheet_id>!A1:B2"``) in one request."""
        data = await self.lark.request("GET", f"/open-apis/sheets/v2/spreadsheets/{self.token}/values_batch_get",
                                       params={"ranges": ",".join(ranges), "valueRenderOption": _RENDER.get(render, render),
                                               "dateTimeRenderOption": "FormattedString"})
        return {item.get("range"): list(item.get("values") or []) for item in data.get("valueRanges") or []}

    async def write_many(self, values: Mapping[str, Sequence[Sequence[Any]]]) -> dict[str, Any]:
        """Writes several ranges in one request."""
        return await self.lark.request("POST", f"/open-apis/sheets/v2/spreadsheets/{self.token}/values_batch_update",
                                       json={"valueRanges": [{"range": cells, "values": [list(row) for row in rows]}
                                                             for cells, rows in values.items()]})


class Sheets(Service):
    async def open(self, spreadsheet: Any) -> Spreadsheet:
        """A spreadsheet by link (sheets or wiki) or token."""
        token, kind, query = token_of(spreadsheet, ("sheet",))
        if kind == "wiki":
            target = (await self.lark.wiki.node(token)).open()
            if not isinstance(target, Spreadsheet):
                raise LookupError("This wiki page does not hold a spreadsheet")
            return await target.refresh()
        sheet = await Spreadsheet(self.lark, {"spreadsheet_token": token}).refresh()
        if query.get("sheet"):
            sheet.raw["_sheet"] = query["sheet"]
        return sheet

    def ref(self, token: str) -> Spreadsheet:
        return Spreadsheet(self.lark, {"spreadsheet_token": token})

    async def create(self, title: str, *, folder: Any = None) -> Spreadsheet:
        folder_token = getattr(folder, "token", None) or (str(folder) if folder else None)
        data = await self.lark.request("POST", "/open-apis/sheets/v3/spreadsheets",
                                       json={"title": title, "folder_token": folder_token})
        return Spreadsheet(self.lark, data.get("spreadsheet") or {})
