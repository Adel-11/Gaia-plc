"""Relational persistence for the V2 PLC model.

The normalized tables below make the model queryable and enforce ownership and
common relationships.  Each revision also stores its complete JSON state so a
load/save cycle does not discard fields introduced by newer UI or engine code.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from backend.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class V2Project(Base):
    __tablename__ = "v2_project"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="Gaia", server_default="Gaia")
    current_revision_id: Mapped[int | None] = mapped_column(ForeignKey("v2_project_revision.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class V2ProjectRevision(Base):
    __tablename__ = "v2_project_revision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1", index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False, default="admin")
    state_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class V2Entity(Base):
    """Common columns for normalized entities; concrete tables have real FKs."""
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class V2ObjectType(V2Entity):
    __tablename__ = "v2_object_type"
    kind: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    communication: Mapped[str | None] = mapped_column(String(256))
    folder: Mapped[str | None] = mapped_column(String(512))


class V2Dut(V2Entity):
    __tablename__ = "v2_dut"
    type_id: Mapped[str | None] = mapped_column(ForeignKey("v2_object_type.id", ondelete="SET NULL"), index=True)
    role: Mapped[str | None] = mapped_column(String(128))


class V2DutMember(V2Entity):
    __tablename__ = "v2_dut_member"
    dut_id: Mapped[str] = mapped_column(ForeignKey("v2_dut.id", ondelete="CASCADE"), index=True)
    section: Mapped[str | None] = mapped_column(String(128))
    iec_type: Mapped[str | None] = mapped_column(String(128))
    default_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)


class V2FunctionBlock(V2Entity):
    __tablename__ = "v2_function_block"
    type_id: Mapped[str | None] = mapped_column(ForeignKey("v2_object_type.id", ondelete="CASCADE"), index=True)
    declaration: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    custom_code: Mapped[str | None] = mapped_column(Text)


class V2Instance(V2Entity):
    __tablename__ = "v2_instance"
    type_id: Mapped[str | None] = mapped_column(ForeignKey("v2_object_type.id", ondelete="SET NULL"), index=True)


class V2TcpClient(V2Entity):
    __tablename__ = "v2_tcp_client"
    host: Mapped[str | None] = mapped_column(String(512))
    port: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(128))
    address_range: Mapped[str | None] = mapped_column(Text)
    word_order: Mapped[str | None] = mapped_column(String(64))


class V2TcpRange(V2Entity):
    __tablename__ = "v2_tcp_range"
    client_id: Mapped[str] = mapped_column(ForeignKey("v2_tcp_client.id", ondelete="CASCADE"), index=True)
    start_address: Mapped[int | None] = mapped_column(Integer)
    end_address: Mapped[int | None] = mapped_column(Integer)


class V2TcpSignal(V2Entity):
    __tablename__ = "v2_tcp_signal"
    client_id: Mapped[str | None] = mapped_column(ForeignKey("v2_tcp_client.id", ondelete="CASCADE"), index=True)
    range_id: Mapped[str | None] = mapped_column(ForeignKey("v2_tcp_range.id", ondelete="SET NULL"), index=True)
    data_type: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[int | None] = mapped_column(Integer)


class V2TcpPoint(V2Entity):
    __tablename__ = "v2_tcp_point"
    signal_id: Mapped[str | None] = mapped_column(ForeignKey("v2_tcp_signal.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(ForeignKey("v2_tcp_client.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="SET NULL"), index=True)
    address: Mapped[int | None] = mapped_column(Integer)
    table_name: Mapped[str | None] = mapped_column(String(64))
    member_name: Mapped[str | None] = mapped_column(String(512))
    word: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str | None] = mapped_column(String(128))
    data_type: Mapped[str | None] = mapped_column(String(128))


class V2RtuLine(V2Entity):
    __tablename__ = "v2_rtu_line"
    port: Mapped[str | None] = mapped_column(String(128))
    baud_rate: Mapped[int | None] = mapped_column(Integer)
    parity: Mapped[str | None] = mapped_column(String(32))
    data_bits: Mapped[int | None] = mapped_column(Integer)
    stop_bits: Mapped[int | None] = mapped_column(Integer)
    timeout: Mapped[str | None] = mapped_column(String(64))


class V2RtuSlave(V2Entity):
    __tablename__ = "v2_rtu_slave"
    line_id: Mapped[str | None] = mapped_column(ForeignKey("v2_rtu_line.id", ondelete="CASCADE"), index=True)
    address: Mapped[int | None] = mapped_column(Integer)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="SET NULL"), index=True)
    device: Mapped[str | None] = mapped_column(String(256))


class V2RtuChannel(Base):
    __tablename__ = "v2_rtu_channel"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    slave_id: Mapped[str | None] = mapped_column(ForeignKey("v2_rtu_slave.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="SET NULL"), index=True)
    name: Mapped[str | None] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    table_name: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[int | None] = mapped_column(Integer)
    channel_offset: Mapped[int | None] = mapped_column(Integer)
    member_name: Mapped[str | None] = mapped_column(String(512))
    data_type: Mapped[str | None] = mapped_column(String(128))
    direction: Mapped[str | None] = mapped_column(String(128))


class V2RtuPoint(V2Entity):
    __tablename__ = "v2_rtu_point"
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("v2_rtu_channel.id", ondelete="CASCADE"), index=True)
    address: Mapped[int | None] = mapped_column(Integer)


class V2Rack(V2Entity):
    __tablename__ = "v2_rack"
    slot: Mapped[int | None] = mapped_column(Integer)


class V2Module(V2Entity):
    __tablename__ = "v2_module"
    rack_id: Mapped[str | None] = mapped_column(ForeignKey("v2_rack.id", ondelete="CASCADE"), index=True)
    slot: Mapped[int | None] = mapped_column(Integer)
    device: Mapped[str | None] = mapped_column(String(256))


class V2IoChannel(V2Entity):
    __tablename__ = "v2_io_channel"
    module_id: Mapped[str | None] = mapped_column(ForeignKey("v2_module.id", ondelete="CASCADE"), index=True)
    channel: Mapped[int | None] = mapped_column(Integer)
    signal: Mapped[str | None] = mapped_column(String(512))
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="SET NULL"), index=True)
    variable: Mapped[str | None] = mapped_column(String(512))
    member_name: Mapped[str | None] = mapped_column(String(512))
    io_type: Mapped[str | None] = mapped_column(String(128))


class V2IoAssignment(V2Entity):
    __tablename__ = "v2_io_assignment"
    module_id: Mapped[str | None] = mapped_column(ForeignKey("v2_module.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("v2_io_channel.id", ondelete="SET NULL"), index=True)


class V2PaValue(V2Entity):
    __tablename__ = "v2_pa_value"
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="CASCADE"), index=True)
    member_name: Mapped[str | None] = mapped_column(String(512))
    data_type: Mapped[str | None] = mapped_column(String(128))
    default_value: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
    supervision_editable: Mapped[bool | None] = mapped_column(nullable=True)


class V2ExchangeFunction(V2Entity):
    __tablename__ = "v2_exchange_function"
    type_a_id: Mapped[str | None] = mapped_column(ForeignKey("v2_object_type.id", ondelete="SET NULL"), index=True)
    type_b_id: Mapped[str | None] = mapped_column(ForeignKey("v2_object_type.id", ondelete="SET NULL"), index=True)
    body: Mapped[str | None] = mapped_column(Text)


class V2ExchangeCopy(V2Entity):
    __tablename__ = "v2_exchange_copy"
    function_id: Mapped[str | None] = mapped_column(ForeignKey("v2_exchange_function.id", ondelete="CASCADE"), index=True)
    source: Mapped[str | None] = mapped_column(String(512))
    destination: Mapped[str | None] = mapped_column(String(512))
    direction: Mapped[str | None] = mapped_column(String(64))


class V2ExchangeCall(V2Entity):
    __tablename__ = "v2_exchange_call"
    function_id: Mapped[str | None] = mapped_column(ForeignKey("v2_exchange_function.id", ondelete="CASCADE"), index=True)
    instance_a_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="CASCADE"), index=True)
    instance_b_id: Mapped[str | None] = mapped_column(ForeignKey("v2_instance.id", ondelete="CASCADE"), index=True)


class V2Function(V2Entity):
    __tablename__ = "v2_function"
    body: Mapped[str | None] = mapped_column(Text)


class V2MappedVariable(V2Entity):
    __tablename__ = "v2_mapped_variable"
    function_id: Mapped[str | None] = mapped_column(ForeignKey("v2_function.id", ondelete="SET NULL"), index=True)
    dut_id: Mapped[str | None] = mapped_column(ForeignKey("v2_dut.id", ondelete="SET NULL"), index=True)


class V2SnapshotObject(V2Entity):
    __tablename__ = "v2_snapshot_object"
    object_type: Mapped[str | None] = mapped_column(String(128))


class V2SnapshotObjectId(V2Entity):
    __tablename__ = "v2_snapshot_object_id"
    object_id: Mapped[str] = mapped_column(ForeignKey("v2_snapshot_object.id", ondelete="CASCADE"), index=True)


class V2CodesysSnapshot(Base):
    __tablename__ = "v2_codesys_snapshot"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class V2ImportBatch(V2Entity):
    __tablename__ = "v2_import_batch"
    created_by: Mapped[str | None] = mapped_column(String(128))


class V2ImportArtifact(V2Entity):
    __tablename__ = "v2_import_artifact"
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("v2_import_batch.id", ondelete="CASCADE"), index=True)
    content: Mapped[str | None] = mapped_column(Text)


class V2WorkbookArtifact(V2Entity):
    __tablename__ = "v2_workbook_artifact"
    workbook_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class V2ExportArtifact(V2Entity):
    __tablename__ = "v2_export_artifact"
    content: Mapped[str | None] = mapped_column(Text)


# State-key aliases cover the names currently emitted by the V2 engine and
# the slightly more descriptive names used by import/export payloads.
_TABLES: dict[str, tuple[type[V2Entity], tuple[str, ...]]] = {
    "types": (V2ObjectType, ("object_types",)),
    "duts": (V2Dut, ()),
    "function_blocks": (V2FunctionBlock, ("fbs",)),
    "instances": (V2Instance, ()),
    "tcp_clients": (V2TcpClient, ()),
    "tcp_ranges": (V2TcpRange, ("tcp_client_ranges",)),
    "tcp_signals": (V2TcpSignal, ()),
    "tcp_points": (V2TcpPoint, ()),
    "rtu_lines": (V2RtuLine, ()),
    "rtu_slaves": (V2RtuSlave, ()),
    "rtu_channels": (V2RtuChannel, ("rtu_line_slave_channels",)),
    "rtu_points": (V2RtuPoint, ("rtu_line_slave_channel_points",)),
    "racks": (V2Rack, ()),
    "modules": (V2Module, ("io_modules",)),
    "io_channels": (V2IoChannel, ()),
    "io_channel_assignments": (V2IoAssignment, ("io_assignments",)),
    "pa_values": (V2PaValue, ()),
    "exchange_functions": (V2ExchangeFunction, ()),
    "exchange_copies": (V2ExchangeCopy, ("exchange_function_copies", "exchange_functions_detail")),
    "exchange_calls": (V2ExchangeCall, ()),
    "functions": (V2Function, ()),
    "mapped_variables": (V2MappedVariable, ()),
    "snapshot_objects": (V2SnapshotObject, ()),
    "snapshot_object_ids": (V2SnapshotObjectId, ()),
    "import_batches": (V2ImportBatch, ()),
    "import_artifacts": (V2ImportArtifact, ()),
    "workbooks": (V2WorkbookArtifact, ("workbook_artifacts",)),
    "exports": (V2ExportArtifact, ("export_artifacts",)),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"value": value}


def _items(value: Any) -> list[tuple[str, dict[str, Any]]]:
    """Normalize list or id->object mappings while keeping source data intact."""
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            obj = _as_mapping(item)
            obj.setdefault("id", str(key))
            out.append((str(key), obj))
        return out
    if isinstance(value, list):
        return [(str(_as_mapping(item).get("id", i)), _as_mapping(item)) for i, item in enumerate(value)]
    return []


def _id(obj: dict[str, Any], fallback: str) -> str:
    raw = obj.get("id", obj.get("key", obj.get("name", fallback)))
    return str(raw)


def _fk(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if value is not None:
            return str(value)
    return None


def _int(obj: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, bool):
            return int(value)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().casefold()
    if normalized in {"yes", "true", "1", "oui", "y"}:
        return True
    if normalized in {"no", "false", "0", "non", "n"}:
        return False
    return None


def _relations(state: dict[str, Any]) -> list[tuple[type[V2Entity], list[dict[str, Any]], Any]]:
    """Build normalized entity rows in FK-safe insert order."""
    rows: list[tuple[type[V2Entity], list[dict[str, Any]], Any]] = []
    for key, (model, aliases) in _TABLES.items():
        raw = next((state[k] for k in (key, *aliases) if k in state), None)
        if key == "workbooks" and isinstance(raw, dict):
            items = [{"id": n, "name": n, "workbook_data": d} for n, d in raw.items()]
        else:
            items = [o for _, o in _items(raw)]
        rows.append((model, items, key))

    # The engine nests DUTs and FB code beneath each ObjectType.
    type_items = next((items for model, items, key in rows if key == "types"), [])
    nested_types: list[dict[str, Any]] = []
    nested_fbs: list[dict[str, Any]] = []
    nested: dict[type[V2Entity], list[dict[str, Any]]] = {
        V2DutMember: [], V2TcpRange: [], V2TcpSignal: [], V2TcpPoint: [],
        V2RtuSlave: [], V2RtuChannel: [], V2RtuPoint: [], V2Module: [],
        V2IoChannel: [], V2IoAssignment: [], V2ExchangeCopy: [], V2ExchangeCall: [],
        V2MappedVariable: [], V2SnapshotObjectId: [],
    }
    for typ in type_items:
        type_id = _id(typ, "type")
        for i, dut in enumerate(typ.get("duts", []) or []):
            obj = copy.deepcopy(_as_mapping(dut)); source_id = _id(obj, f"dut:{i}"); obj["id"] = f"{type_id}:{source_id}"; obj.setdefault("type_id", type_id)
            nested_types.append(obj)
            for mi, member in enumerate(obj.get("members", []) or []):
                member_obj = copy.deepcopy(_as_mapping(member))
                member_obj["id"] = f"{obj['id']}:{_id(member_obj, f'member:{mi}')}"
                member_obj["dut_id"] = obj["id"]
                nested.setdefault(V2DutMember, []).append(member_obj)
        fb = typ.get("fb")
        if isinstance(fb, dict):
            obj = copy.deepcopy(fb); obj.setdefault("id", f"{type_id}:fb"); obj.setdefault("name", obj.get("name", typ.get("name"))); obj.setdefault("type_id", type_id)
            nested_fbs.append(obj)
    # Keep nested input objects queryable even though the full revision remains
    # the source of truth for exact read-back.
    if nested_types:
        rows.append((V2Dut, nested_types, "nested_duts"))
    if nested_fbs:
        rows.append((V2FunctionBlock, nested_fbs, "nested_fbs"))

    # Expand nested model components, preserving them in the parent payload too.
    by_name = {m.__tablename__: (m, items) for m, items, _ in rows}
    def get(cls): return by_name[cls.__tablename__][1]
    nested_dut_ids = {_id(d, "dut") for d in nested_types}
    for dut in get(V2Dut):
        if _id(dut, "dut") in nested_dut_ids:
            continue
        for i, item in enumerate(dut.get("members", []) or []):
            o = _as_mapping(item); o.setdefault("id", f"{_id(dut,'dut')}:member:{i}"); o.setdefault("dut_id", _id(dut,'dut')); nested[V2DutMember].append(o)
    # The entity list may combine top-level and nested records; gather all rows
    # for each owner so children have a parent before insertion.
    for owner, field, child_cls, fkname in (
        (V2TcpClient,"ranges",V2TcpRange,"client_id"), (V2TcpClient,"signals",V2TcpSignal,"client_id"),
        (V2TcpClient,"points",V2TcpPoint,"client_id"), (V2RtuLine,"slaves",V2RtuSlave,"line_id"),
        (V2RtuSlave,"channels",V2RtuChannel,"slave_id"), (V2RtuChannel,"points",V2RtuPoint,"channel_id"),
        (V2Rack,"modules",V2Module,"rack_id"), (V2Module,"channels",V2IoChannel,"module_id"),
        (V2Module,"assignments",V2IoAssignment,"module_id"), (V2ExchangeFunction,"copies",V2ExchangeCopy,"function_id"),
        (V2ExchangeFunction,"calls",V2ExchangeCall,"function_id"), (V2Function,"mapped_variables",V2MappedVariable,"function_id"),
        (V2SnapshotObject,"ids",V2SnapshotObjectId,"object_id"),
    ):
        owner_items = get(owner)
        if owner is V2ObjectType:
            owner_items = type_items
        for parent in owner_items:
            pid = _id(parent, owner.__tablename__)
            for i, child in enumerate(parent.get(field, []) or []):
                o = _as_mapping(child); o.setdefault("id", f"{pid}:{field}:{i}"); o.setdefault(fkname,pid); nested[child_cls].append(o)
    # Flat RTU channel rows reference a slave by instance name in engine data.
    slaves = next((items for model, items, key in rows if key == "rtu_slaves"), [])
    channels = next((items for model, items, key in rows if key == "rtu_channels"), [])
    for channel in channels:
        if not channel.get("slave_id"):
            slave = next((s for s in slaves if str(s.get("instance_id", "")) == str(channel.get("instance_id", "")) or str(s.get("id", "")) == str(channel.get("instance_id", ""))), None)
            if slave:
                channel["slave_id"] = _id(slave, "slave")
    exchange_functions = next((items for model, items, key in rows if key == "exchange_functions"), [])
    exchange_copies = next((items for model, items, key in rows if key == "exchange_copies"), [])
    function_by_sheet = {str(fn.get("sheet")): _id(fn, "exchange-function") for fn in exchange_functions if fn.get("sheet")}
    for copy_row in exchange_copies:
        if not copy_row.get("function_id"):
            sheet = copy_row.get("exchange_function_id", copy_row.get("sheet"))
            if sheet is not None:
                copy_row["function_id"] = function_by_sheet.get(str(sheet), str(sheet))
    snapshot = state.get("snapshot")
    if isinstance(snapshot, dict):
        for i, item in enumerate(snapshot.get("objects", []) or []):
            obj = copy.deepcopy(_as_mapping(item)); obj.setdefault("id", obj.get("name", f"snapshot-object:{i}")); obj.setdefault("object_type", obj.get("kind")); nested.setdefault(V2SnapshotObject, []).append(obj)
            if obj.get("object_id") or obj.get("id_string"):
                nested.setdefault(V2SnapshotObjectId, []).append({"id": f"{obj['id']}:objectid", "object_id": obj["id"], "name": obj.get("object_id", obj.get("id_string"))})
        raw_ids = snapshot.get("object_ids", {})
        if isinstance(raw_ids, dict):
            objects_by_name = {str(o.get("name")): o for o in nested.get(V2SnapshotObject, [])}
            for name, value in raw_ids.items():
                object_row = objects_by_name.get(str(name))
                if object_row is None:
                    object_row = {"id": str(name), "name": str(name), "object_type": "unknown"}
                    nested.setdefault(V2SnapshotObject, []).append(object_row)
                    objects_by_name[str(name)] = object_row
                object_id = _id(object_row, str(name))
                nested.setdefault(V2SnapshotObjectId, []).append({"id": f"{name}:objectid", "object_id": object_id, "name": str(value)})
    # Preserve source-key collections before expanded nested rows.
    explicit_models = {m for m, _, _ in rows if m not in (V2Dut, V2FunctionBlock)}
    for cls, items in nested.items():
        match = next((index for index, (model, _, _) in enumerate(rows) if model is cls), None)
        if match is None:
            rows.append((cls, items, cls.__tablename__))
        else:
            model, existing_items, key = rows[match]
            rows[match] = (model, existing_items + items, key)
    return rows


def _relational_values(model: type[V2Entity], obj: dict[str, Any], pos: int) -> dict[str, Any]:
    vals: dict[str, Any] = {"id": _id(obj, f"{model.__tablename__}:{pos}"), "name": obj.get("name"), "position": _int(obj,"position","order") or pos, "payload": _json(obj)}
    tab = model.__tablename__
    if tab == "v2_object_type": vals.update(kind=obj.get("kind",obj.get("nature")), description=obj.get("description"), communication=obj.get("communication"), folder=obj.get("folder",obj.get("project_folder")))
    elif tab == "v2_dut": vals.update(type_id=_fk(obj,"type_id","object_type_id"), role=obj.get("role"))
    elif tab == "v2_dut_member": vals.update(dut_id=_fk(obj,"dut_id") or "", section=obj.get("section"), iec_type=obj.get("iec_type",obj.get("type")), default_value=None if obj.get("default_value") is None else str(obj.get("default_value")), unit=obj.get("unit"), comment=obj.get("comment"))
    elif tab == "v2_function_block": vals.update(type_id=_fk(obj,"type_id"), declaration=obj.get("declaration"), body=obj.get("body"), custom_code=obj.get("custom_code"))
    elif tab == "v2_instance": vals.update(type_id=_fk(obj,"type_id","object_type_id"))
    elif tab == "v2_tcp_client": vals.update(host=obj.get("host",obj.get("ip_address",obj.get("ip"))), port=_int(obj,"port"), role=obj.get("role"), address_range=_json(obj.get("address_range")) if obj.get("address_range") is not None else None, word_order=obj.get("word_order"))
    elif tab == "v2_tcp_range": vals.update(client_id=_fk(obj,"client_id") or "", start_address=_int(obj,"start_address","start"), end_address=_int(obj,"end_address","end"))
    elif tab == "v2_tcp_signal": vals.update(client_id=_fk(obj,"client_id"), range_id=_fk(obj,"range_id"), data_type=obj.get("data_type",obj.get("iec_type")), address=_int(obj,"address"))
    elif tab == "v2_tcp_point": vals.update(signal_id=_fk(obj,"signal_id"), client_id=_fk(obj,"client_id"), instance_id=_fk(obj,"instance_id"), address=_int(obj,"address"), table_name=obj.get("table",obj.get("table_name")), member_name=obj.get("member",obj.get("member_name")), word=None if obj.get("word") is None else str(obj.get("word")), direction=obj.get("direction"), data_type=obj.get("data_type",obj.get("iec_type")))
    elif tab == "v2_rtu_slave": vals.update(line_id=_fk(obj,"line_id"), address=_int(obj,"address","slave_id"))
    elif tab == "v2_rtu_line": vals.update(port=obj.get("port"), baud_rate=_int(obj,"baud_rate","baud"), parity=obj.get("parity"), data_bits=_int(obj,"data_bits"), stop_bits=_int(obj,"stop_bits"), timeout=None if obj.get("timeout", obj.get("timeout_ms")) is None else str(obj.get("timeout", obj.get("timeout_ms"))))
    elif tab == "v2_rtu_channel": vals.update(slave_id=_fk(obj,"slave_id"), instance_id=_fk(obj,"instance_id"), table_name=obj.get("table"), address=_int(obj,"address"), channel_offset=_int(obj,"channel_offset"), member_name=obj.get("member_name",obj.get("member")), data_type=obj.get("data_type"), direction=obj.get("direction"))
    elif tab == "v2_rtu_point": vals.update(channel_id=_fk(obj,"channel_id"), address=_int(obj,"address"))
    elif tab == "v2_module": vals.update(rack_id=_fk(obj,"rack_id"), slot=_int(obj,"slot"), device=obj.get("device"))
    elif tab == "v2_rack": vals.update(slot=_int(obj,"slot"))
    elif tab == "v2_io_channel": vals.update(module_id=_fk(obj,"module_id"), channel=_int(obj,"channel","index"), signal=obj.get("signal"), instance_id=_fk(obj,"instance_id"), variable=obj.get("variable"), member_name=obj.get("member_name"), io_type=obj.get("io_type"))
    elif tab == "v2_io_assignment": vals.update(module_id=_fk(obj,"module_id"), channel_id=_fk(obj,"channel_id"))
    elif tab == "v2_pa_value": vals.update(instance_id=_fk(obj,"instance_id"), member_name=obj.get("member",obj.get("member_name")), data_type=obj.get("data_type"), default_value=None if obj.get("default_value") is None else str(obj.get("default_value")), value=None if obj.get("value") is None else str(obj.get("value")), supervision_editable=_bool_or_none(obj.get("supervision_editable",obj.get("editable_from_supervision"))))
    elif tab == "v2_exchange_function": vals.update(type_a_id=_fk(obj,"type_a_id","typeA_id"), type_b_id=_fk(obj,"type_b_id","typeB_id"), body=obj.get("body"))
    elif tab == "v2_exchange_copy": vals.update(function_id=_fk(obj,"function_id","exchange_function_id"), source=obj.get("source"), destination=obj.get("destination"), direction=obj.get("direction"))
    elif tab == "v2_exchange_call": vals.update(function_id=_fk(obj,"function_id","exchange_function_id"), instance_a_id=_fk(obj,"instance_a_id","instanceA_id"), instance_b_id=_fk(obj,"instance_b_id","instanceB_id"))
    elif tab == "v2_function": vals.update(body=obj.get("body",obj.get("code")))
    elif tab == "v2_mapped_variable": vals.update(function_id=_fk(obj,"function_id"), dut_id=_fk(obj,"dut_id"))
    elif tab == "v2_snapshot_object": vals.update(object_type=obj.get("object_type",obj.get("type")))
    elif tab == "v2_snapshot_object_id": vals.update(object_id=_fk(obj,"object_id") or "")
    elif tab == "v2_import_batch": vals.update(created_by=obj.get("created_by"))
    elif tab == "v2_import_artifact": vals.update(batch_id=_fk(obj,"batch_id"), content=obj.get("content"))
    elif tab == "v2_workbook_artifact": vals.update(workbook_data=_json(obj.get("workbook_data",obj)))
    elif tab == "v2_export_artifact": vals.update(content=obj.get("content"))
    return vals


def _replace_relational(session: Session, state: dict[str, Any]) -> None:
    # Delete in reverse FK dependency order so SQLite with foreign_keys enabled
    # behaves the same as production databases.
    from sqlalchemy import delete
    rows = _relations(state)
    session.execute(delete(V2CodesysSnapshot))
    snapshot = state.get("snapshot")
    if isinstance(snapshot, dict):
        session.add(V2CodesysSnapshot(id=1, payload=_json(snapshot), imported_at=_now()))
    models = {model for model, _, _ in rows}
    table_models = {model.__table__: model for model in models}
    for table in reversed(Base.metadata.sorted_tables):
        model = table_models.get(table)
        if model is not None:
            session.execute(delete(model))
    objects_by_model: dict[type[V2Entity], list[dict[str, Any]]] = {}
    for model, objects, _ in rows:
        objects_by_model.setdefault(model, []).extend(objects)
    for table in Base.metadata.sorted_tables:
        model = table_models.get(table)
        if model is None:
            continue
        seen: set[str] = set()
        batch: list[V2Entity] = []
        for pos, obj in enumerate(objects_by_model.get(model, [])):
            vals = _relational_values(model, obj, pos)
            # A child explicitly present at the top level takes precedence over
            # an identical copy expanded from a parent's nested collection.
            if vals["id"] in seen:
                continue
            seen.add(vals["id"])
            batch.append(model(**vals))
        if batch:
            session.add_all(batch)
            session.flush()


def load_project(session: Session) -> dict[str, Any] | None:
    from backend.v2_workspace import load_workspace_project
    return load_workspace_project(session)


def save_project(session: Session, state: dict[str, Any], username: str = "admin", expected_revision: int | None = None) -> dict[str, Any]:
    from backend.v2_workspace import save_workspace_project
    return save_workspace_project(session, state, username, expected_revision)


def initialize(session: Session, state: dict[str, Any]) -> dict[str, Any]:
    """Initialize V2 once. Existing projects are returned unchanged."""
    existing = load_project(session)
    if existing is not None:
        return existing
    return save_project(session, state, username="system", expected_revision=0)


# Register workspace tables whenever the shared persistence metadata is imported.
from backend import v2_workspace as _workspace_models  # noqa: E402,F401
