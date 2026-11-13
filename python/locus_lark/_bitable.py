"""Bases (multidimensional tables): tables, fields, views and records.

A ``Record`` behaves like a dict of field name to value; change it and
``await record.save()`` writes the changed fields. Bulk methods on ``Table``
send up to 500 records per request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, MutableMapping
from typing import Any

from ._cloud import CloudObject
from ._core import Resource, Service
from ._util import from_timestamp, token_of

_BATCH = 500
FIELD_TYPES = {
    "text": 1, "number": 2, "single_select": 3, "multi_select": 4, "date": 5, "checkbox": 7, "user": 11,
    "phone": 13, "url": 15, "attachment": 17, "link": 18, "lookup": 19, "formula": 20, "duplex_link": 21,
    "location": 22, "group": 23, "created_time": 1001, "modified_time": 1002, "created_by": 1003,
    "modified_by": 1004, "auto_number": 1005,
}
_OPERATORS = {"==": "is", "!=": "isNot", "contains": "contains", "not_contains": "doesNotContain",
              "empty": "isEmpty", "not_empty": "isNotEmpty", ">": "isGreater", ">=": "isGreaterEqual",
              "<": "isLess", "<=": "isLessEqual"}


def where_filter(where: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A filter from ``{"Status": "Done", "Score": (">=", 80), "Owner": ("empty",)}``
    (all must match). A dict with ``conditions`` passes through unchanged."""
    if not where:
        return None
    if "conditions" in where:
        return dict(where)
    conditions = []
    for field, condition in where.items():
        if isinstance(condition, tuple):
            operator = _OPERATORS.get(condition[0], condition[0])
            values = list(condition[1:])
        else:
            operator, values = "is", [condition]
        conditions.append({"field_name": field, "operator": operator,
                           "value": [value if isinstance(value, str) else str(value) for value in values]})
    return {"conjunction": "and", "conditions": conditions}


class Field(Resource):
    _id_key = "field_id"

    @property
    def name(self) -> str:
        return str(self.raw.get("field_name") or "")

    @property
    def type(self) -> int:
        return int(self.raw.get("type") or 0)

    @property
    def options(self) -> list[str]:
        """Choices of a select field."""
        return [str(option.get("name")) for option in (self.raw.get("property") or {}).get("options") or []]

    @property
    def is_primary(self) -> bool:
        return bool(self.raw.get("is_primary"))


class View(Resource):
    _id_key = "view_id"

    @property
    def name(self) -> str:
        return str(self.raw.get("view_name") or "")

    @property
    def type(self) -> str:
        return str(self.raw.get("view_type") or "")


class Record(Resource, MutableMapping):
    """One row. ``record["Name"]`` reads and writes field values; ``save()``
    sends the changes."""

    _id_key = "record_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], table: "Table") -> None:
        Resource.__init__(self, lark, raw)
        self.table = table
        self._changed: set[str] = set()

    @property
    def fields(self) -> dict[str, Any]:
        return self.raw.setdefault("fields", {})

    @property
    def created_at(self) -> Any:
        return from_timestamp(self.raw.get("created_time"))

    @property
    def modified_at(self) -> Any:
        return from_timestamp(self.raw.get("last_modified_time"))

    def __getitem__(self, name: str) -> Any:
        return self.fields[name]

    def __setitem__(self, name: str, value: Any) -> None:
        self.fields[name] = value
        self._changed.add(name)

    def __delitem__(self, name: str) -> None:
        self.fields[name] = None
        self._changed.add(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self.fields)

    def __len__(self) -> int:
        return len(self.fields)

    __eq__ = Resource.__eq__
    __hash__ = Resource.__hash__

    @property
    def dirty(self) -> bool:
        return bool(self._changed)

    def _label(self) -> str:
        first = next(iter(self.fields.values()), None)
        return f" {str(first)[:40]!r}" if first is not None else ""

    async def save(self) -> "Record":
        """Writes the changed fields."""
        if not self._changed:
            return self
        changes = {name: self.fields.get(name) for name in self._changed}
        data = await self.lark.request("PUT", f"{self.table._base}/records/{self.id}", json={"fields": changes})
        self.raw.update(data.get("record") or {})
        self._changed.clear()
        return self

    async def refresh(self) -> "Record":
        data = await self.lark.request("GET", f"{self.table._base}/records/{self.id}")
        self.raw.update(data.get("record") or {})
        self._changed.clear()
        return self

    async def delete(self) -> None:
        await self.lark.request("DELETE", f"{self.table._base}/records/{self.id}")


class Table(Resource):
    """A table of a base (``id`` is the ``table_id``)."""

    _id_key = "table_id"

    def __init__(self, lark: Any, raw: Mapping[str, Any], base: "Base") -> None:
        super().__init__(lark, raw)
        self.base = base

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    @property
    def _base(self) -> str:
        return f"/open-apis/bitable/v1/apps/{self.base.token}/tables/{self.id}"

    # ── schema ──────────────────────────────────────────────────────────

    async def fields(self) -> list[Field]:
        return [Field(self.lark, item) async for item in self.lark.pages("GET", f"{self._base}/fields", page_size=100)]

    async def add_field(self, name: str, type: str | int = "text", **property: Any) -> Field:
        """Adds a field (``type`` a name from ``FIELD_TYPES`` or its number;
        ``property`` such as ``options=[{"name": "A"}]`` for selects)."""
        number = FIELD_TYPES.get(type, type) if isinstance(type, str) else type
        data = await self.lark.request("POST", f"{self._base}/fields",
                                       json={"field_name": name, "type": number, "property": property or None})
        return Field(self.lark, data.get("field") or {})

    async def update_field(self, field: Field | str, *, name: str | None = None, type: str | int | None = None,
                           **property: Any) -> Field:
        current = field if isinstance(field, Field) else next(item for item in await self.fields() if field in (item.id, item.name))
        number = FIELD_TYPES.get(type, type) if isinstance(type, str) else (type or current.type)
        data = await self.lark.request("PUT", f"{self._base}/fields/{current.id}",
                                       json={"field_name": name or current.name, "type": number,
                                             "property": property or current.raw.get("property")})
        return Field(self.lark, data.get("field") or {})

    async def delete_field(self, field: Field | str) -> None:
        field_id = field.id if isinstance(field, Field) else next(item.id for item in await self.fields() if field in (item.id, item.name))
        await self.lark.request("DELETE", f"{self._base}/fields/{field_id}")

    async def views(self) -> list[View]:
        return [View(self.lark, item) async for item in self.lark.pages("GET", f"{self._base}/views", page_size=100)]

    # ── records ─────────────────────────────────────────────────────────

    async def records(self, *, where: Mapping[str, Any] | None = None, view: View | str | None = None,
                      fields: Iterable[str] | None = None, sort: Iterable[str] | None = None,
                      limit: int | None = None) -> AsyncIterator[Record]:
        """Records matching ``where`` (see ``where_filter``), in a view's
        order or sorted by field names (``"-Score"`` descending)."""
        body: dict[str, Any] = {"automatic_fields": True}
        view_id = view.id if isinstance(view, View) else (view or self.raw.get("_view"))
        if view_id:
            body["view_id"] = view_id
        if fields:
            body["field_names"] = list(fields)
        if sort:
            body["sort"] = [{"field_name": name.lstrip("-"), "desc": name.startswith("-")} for name in sort]
        condition = where_filter(where)
        if condition:
            body["filter"] = condition
        async for item in self.lark.pages("POST", f"{self._base}/records/search", json=body, page_size=_BATCH, limit=limit):
            yield Record(self.lark, item, self)

    async def first(self, **where: Any) -> Record | None:
        """The first record whose fields equal ``where``."""
        async for record in self.records(where=where, limit=1):
            return record
        return None

    async def get(self, record_id: str) -> Record:
        return await Record(self.lark, {"record_id": record_id}, self).refresh()

    async def add(self, fields: Mapping[str, Any] | None = None, **values: Any) -> Record:
        """Adds one record from a dict of field values and/or keyword values."""
        data = await self.lark.request("POST", f"{self._base}/records", json={"fields": {**(fields or {}), **values}})
        return Record(self.lark, data.get("record") or {}, self)

    async def add_many(self, rows: Iterable[Mapping[str, Any]]) -> list[Record]:
        rows = list(rows)
        created: list[Record] = []
        for start in range(0, len(rows), _BATCH):
            data = await self.lark.request("POST", f"{self._base}/records/batch_create",
                                           json={"records": [{"fields": dict(row)} for row in rows[start:start + _BATCH]]})
            created.extend(Record(self.lark, item, self) for item in data.get("records") or [])
        return created

    async def update_many(self, records: Iterable[Record | tuple[str, Mapping[str, Any]]]) -> list[Record]:
        """Saves changed Records, or ``(record_id, fields)`` pairs."""
        items: list[dict[str, Any]] = []
        saved: list[list[Record]] = []
        for record in records:
            if isinstance(record, Record):
                if record.dirty:
                    items.append({"record_id": record.id, "fields": {name: record.fields.get(name) for name in record._changed}})
                    saved.append([record])
            else:
                items.append({"record_id": record[0], "fields": dict(record[1])})
                saved.append([])
        updated: list[Record] = []
        for start in range(0, len(items), _BATCH):
            data = await self.lark.request("POST", f"{self._base}/records/batch_update", json={"records": items[start:start + _BATCH]})
            updated.extend(Record(self.lark, item, self) for item in data.get("records") or [])
            # Only changes the server took count as saved.
            for group in saved[start:start + _BATCH]:
                for record in group:
                    record._changed.clear()
        return updated

    async def delete_many(self, records: Iterable[Record | str]) -> None:
        ids = [record.id if isinstance(record, Record) else str(record) for record in records]
        for start in range(0, len(ids), _BATCH):
            await self.lark.request("POST", f"{self._base}/records/batch_delete", json={"records": ids[start:start + _BATCH]})

    async def rename(self, name: str) -> None:
        await self.lark.request("PATCH", self._base, json={"name": name})
        self.raw["name"] = name

    async def delete(self) -> None:
        await self.lark.request("DELETE", self._base)


class Base(CloudObject):
    """A base (``id`` is the ``app_token``). Also offers comments,
    collaborators, sharing and export."""

    _id_key = "app_token"
    doc_type = "bitable"

    @property
    def name(self) -> str:
        return str(self.raw.get("name") or "")

    async def refresh(self) -> "Base":
        data = await self.lark.request("GET", f"/open-apis/bitable/v1/apps/{self.token}")
        self.raw.update(data.get("app") or {})
        return self

    async def tables(self) -> list[Table]:
        return [Table(self.lark, item, self) async for item in
                self.lark.pages("GET", f"/open-apis/bitable/v1/apps/{self.token}/tables", page_size=100)]

    async def table(self, which: str | int = 0) -> Table:
        """A table by name, id or position (a link's ``?table=`` wins for ``0``)."""
        tables = await self.tables()
        if which == 0 and self.raw.get("_table"):
            which = self.raw["_table"]
        if isinstance(which, int):
            return tables[which]
        for table in tables:
            if which in (table.name, table.id):
                if self.raw.get("_view") and table.id == self.raw.get("_table"):
                    table.raw["_view"] = self.raw["_view"]
                return table
        raise LookupError(f"No table {which!r} in {self.name or self.token}")

    async def create_table(self, name: str, fields: Iterable[Mapping[str, Any] | str] = ()) -> Table:
        """Adds a table; ``fields`` are names (text fields) or ``{"field_name", "type"}`` dicts."""
        specs = [{"field_name": field, "type": 1} if isinstance(field, str) else
                 {**field, "type": FIELD_TYPES.get(field.get("type"), field.get("type", 1))} for field in fields]
        data = await self.lark.request("POST", f"/open-apis/bitable/v1/apps/{self.token}/tables",
                                       json={"table": {"name": name, "fields": specs or None}})
        return Table(self.lark, {"table_id": data.get("table_id"), "name": name}, self)


class Bases(Service):
    async def open(self, base: Any) -> Base:
        """A base by link (base or wiki; ``?table=`` and ``?view=`` are kept) or token."""
        token, kind, query = token_of(base, ("bitable",))
        if kind == "wiki":
            target = (await self.lark.wiki.node(token)).open()
            if not isinstance(target, Base):
                raise LookupError("This wiki page does not hold a base")
            return await target.refresh()
        result = await Base(self.lark, {"app_token": token}).refresh()
        for key, name in (("table", "_table"), ("view", "_view")):
            if query.get(key):
                result.raw[name] = query[key]
        return result

    def ref(self, app_token: str) -> Base:
        return Base(self.lark, {"app_token": app_token})

    async def create(self, name: str, *, folder: Any = None) -> Base:
        folder_token = getattr(folder, "token", None) or (str(folder) if folder else None)
        data = await self.lark.request("POST", "/open-apis/bitable/v1/apps", json={"name": name, "folder_token": folder_token})
        return Base(self.lark, data.get("app") or {})
