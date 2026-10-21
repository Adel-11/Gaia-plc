"""Workbook-backed v2 project model and round-trip helpers.

The v2 project files are deliberately treated as the source of truth.  Alongside
the normalized objects, ``state['workbooks']`` retains every populated cell so
that unknown columns, TO CHECK markers, and workbook-specific row order survive
an import/export cycle.
"""
from __future__ import annotations

import base64
import copy
import csv
from datetime import datetime, timezone
import functools
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = ROOT / "reference-v2"
TABLES = {
    "COILS": "coils", "DISCRETE_INPUTS": "discrete_inputs",
    "INPUT_REGISTERS": "input_registers", "HOLDING_REGISTERS": "holding_registers",
}
TYPE_FILES = {"TYPE_ADM100W.xlsx", "TYPE_DIP1500.xlsx", "TYPE_DOSENSOR.xlsx",
              "TYPE_UPI_LOCAL_FUXA.xlsx", "TYPE_UPI_LOCAL_HMI.xlsx",
              "TYPE_UPI_LOCAL_WEBVISU.xlsx"}
PROJECT_FILES = {"MODBUS_TCP.xlsx", "MODBUS_RTU.xlsx", "IO_PHYSICAL.xlsx",
                 "PARAMETERS_PA.xlsx", "EXCHANGES_EXC.xlsx"}
ALLOWED_WORKBOOKS = TYPE_FILES | PROJECT_FILES
UNSAFE_SUFFIXES = {".xls", ".xlsm", ".xlsb", ".xlt", ".xltm", ".xltx"}


def _read_wb(data: bytes, *, data_only: bool = True):
    return openpyxl.load_workbook(io.BytesIO(data), data_only=data_only)


def _cell_value(value: Any) -> Any:
    # Keep scalar types (and None) verbatim.  Dates remain ISO strings to keep
    # the public state JSON serializable without losing their displayed value.
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def workbook_to_data(data: bytes, filename: str) -> dict:
    """Read populated XML cells directly; formatted validation ranges can be 2,000 rows."""
    import xml.etree.ElementTree as ET

    zf = zipfile.ZipFile(io.BytesIO(data))
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root.findall(f"{rel_ns}Relationship")}
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    sst = []
    if "xl/sharedStrings.xml" in zf.namelist():
        ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        sst = ["".join(t.text or "" for t in item.iter(f"{main_ns}t")) for item in ss_root.findall(f"{main_ns}si")]
    sheets = []
    for sheet_el in workbook_root.findall(f"{main_ns}sheets/{main_ns}sheet"):
        target = rels[sheet_el.attrib[f"{{http://schemas.openxmlformats.org/officeDocument/2006/relationships}}id"]]
        part = target.lstrip("/") if target.startswith("/") else str(Path("xl") / target)
        # Targets in Excel files are relative to xl/. Normalize ../ where present.
        part = str(Path(part)).replace("\\", "/")
        root = ET.fromstring(zf.read(part))
        by_row: dict[int, dict[int, Any]] = {}
        max_row = 0; max_col = 0
        for row_el in root.findall(f"{main_ns}sheetData/{main_ns}row"):
            row_no = int(row_el.attrib.get("r", "0"))
            cells = by_row.setdefault(row_no, {})
            for cell in row_el.findall(f"{main_ns}c"):
                coord = cell.attrib.get("r", "")
                col = 0
                for char in re.match(r"[A-Z]+", coord).group(0):
                    col = col * 26 + ord(char) - 64
                typ = cell.attrib.get("t", "n")
                value_el = cell.find(f"{main_ns}v")
                value: Any = None
                if typ == "inlineStr":
                    inline = cell.find(f"{main_ns}is")
                    value = "".join(t.text or "" for t in inline.iter(f"{main_ns}t")) if inline is not None else ""
                elif value_el is not None and value_el.text is not None:
                    raw = value_el.text
                    if typ == "s": value = sst[int(raw)]
                    elif typ == "b": value = raw == "1"
                    elif typ in {"str", "e"}: value = raw
                    else:
                        try:
                            numeric = float(raw)
                            value = int(numeric) if numeric.is_integer() else numeric
                        except ValueError:
                            value = raw
                cells[col] = value
                if value is not None:
                    max_row = max(max_row, row_no); max_col = max(max_col, col)
        values = []
        for r in range(1, max_row + 1):
            by_col = by_row.get(r, {})
            values.append([by_col.get(c) for c in range(1, max_col + 1)])
        headers, rows = (values[0], values[1:]) if values else ([], [])
        sheets.append({"name": sheet_el.attrib["name"], "state": sheet_el.attrib.get("state", "visible"),
                       "headers": headers, "rows": rows})
    return {"filename": filename, "sheets": sheets}


def _sheet_map(book: dict) -> dict[str, dict]:
    return {s["name"]: s for s in book.get("sheets", [])}


def _objects(book: dict, name: str) -> list[dict]:
    sheet = _sheet_map(book).get(name)
    if not sheet:
        return []
    headers = [str(h) if h is not None else "" for h in sheet.get("headers", [])]
    result = []
    for values in sheet.get("rows", []):
        row = dict(zip(headers, values))
        if any(v is not None and v != "" for v in row.values()):
            result.append(row)
    return result


def _member_from_var(value: Any) -> str | None:
    if value in (None, ""):
        return None
    parts = str(value).split(".")
    if len(parts) >= 3 and parts[0] in {"GVL_COM", "GVL_IO"}:
        value = parts[2]
    else:
        value = parts[-1]
    return re.sub(r"\[[^\]]*\](?:\.\d+)?$", "", str(value))


def _normalize_type(book: dict) -> dict:
    sheets = _sheet_map(book)
    pairs = _objects(book, "TYPE")
    metadata = {str(r.get("Key")): r.get("Value") for r in pairs if r.get("Key")}
    name = metadata.get("Object type") or Path(book["filename"]).stem.removeprefix("TYPE_")
    duts = []
    for dut_name in ("DUT_COM", "DUT_EXC", "DUT_INT", "DUT_IO", "DUT_PA"):
        if dut_name not in sheets or metadata.get(dut_name) in (None, ""):
            continue
        members, section = [], ""
        for row in _objects(book, dut_name):
            if row.get("Section"):
                section = row["Section"]
            if not row.get("Member"):
                continue
            members.append({"id": f"{name}:{dut_name}:{len(members)}", "position": len(members),
                            "section": section, "name": row.get("Member"),
                            "iec_type": row.get("Type"), "default_value": row.get("Default value"),
                            "unit": row.get("Unit"), "comment": row.get("Comment"),
                            "exchange": row.get("Exchange")})
        dut_object_name = str(metadata.get(dut_name) or dut_name)
        duts.append({"id": f"{name}:{dut_name}", "name": dut_object_name,
                     "role": dut_name.removeprefix("DUT_"), "members": members})
    rtu = {s: _objects(book, s) for s in ("RTU_COILS", "RTU_DI", "RTU_IR", "RTU_HR")}
    templates = {s: _objects(book, s) for s in ("TEMPLATE_PA", "TEMPLATE_RTU", "TEMPLATE_EXC", "TEMPLATE_TCP", "TEMPLATE_IO")}
    return {"id": str(name), "name": str(name), "kind": metadata.get("Kind", ""),
            "description": metadata.get("Description", ""), "folder": metadata.get("Project folder", ""),
            "communication": metadata.get("Communication", ""),
            "metadata": metadata, "duts": duts, "fb": {"name": metadata.get("Function block", metadata.get("FB", "")),
            "declaration": "", "body": "", "custom_code": ""},
            "rtu_default": {str(r.get("Key")): r.get("Value") for r in _objects(book, "RTU_DEFAULT")},
            "rtu": rtu, "templates": templates, "source_file": book["filename"]}


def _excel_state(state: dict, book: dict) -> None:
    """Derive normalized project entities from retained project workbook rows."""
    filename = book.get("filename", "")
    sm = _sheet_map(book)
    if filename == "MODBUS_TCP.xlsx":
        state["tcp_clients"] = []
        for row in _objects(book, "CLIENTS"):
            state["tcp_clients"].append({**row, "id": str(row.get("Client", "")), "name": row.get("Client", ""),
                                          "role": row.get("Role"), "ip_address": row.get("IP address"),
                                          "address_range": row.get("Address range"), "word_order": row.get("Word order (32-bit)"),
                                          "comment": row.get("Comment")})
        state["tcp_points"] = []
        for sheet_name, table in TABLES.items():
            for row in _objects(book, sheet_name):
                row = dict(row); row["table"] = table; row["sheet"] = sheet_name
                row["id"] = f"{table}:{row.get('Client','')}:{row.get('Modbus address','')}"
                row["address"] = row.get("Modbus address")
                row["client_id"] = str(row.get("Client", "")) or None
                row["instance_id"] = str(row.get("Instance", "")) or None
                row["member_name"] = _member_from_var(row.get("GVL_COM variable"))
                row["direction"] = row.get("Direction")
                row["data_type"] = row.get("Type")
                row["word"] = row.get("Word")
                row["name"] = row.get("Name")
                state["tcp_points"].append(row)
    elif filename == "MODBUS_RTU.xlsx":
        state["rtu_lines"] = [{**r, "id": str(r.get("Line", "")), "name": r.get("Line", ""),
                               "port": r.get("PLC port"), "baud_rate": r.get("Baud rate"), "parity": r.get("Parity"),
                               "data_bits": r.get("Data bits"), "stop_bits": r.get("Stop bits"),
                               "timeout_ms": r.get("Response timeout (ms)")} for r in _objects(book, "LINES")]
        state["rtu_slaves"] = [{**r, "id": str(r.get("Instance", "")), "name": r.get("Instance"),
                                "instance_id": str(r.get("Instance", "")), "type_id": str(r.get("Object type", "")),
                                "line_id": str(r.get("Line", "")), "address": r.get("Slave address"),
                                "device": r.get("CODESYS slave device"), "word_order": r.get("32-bit word order")} for r in _objects(book, "SLAVES")]
        state["rtu_channels"] = []
        for sheet in book.get("sheets", []):
            if sheet["name"] not in {"README", "LINES", "SLAVES", "LISTS"}:
                for row in _objects(book, sheet["name"]):
                    row = dict(row); row["id"] = f"{sheet['name']}:{row.get('Table')}:{row.get('Equipment address')}"
                    row["instance"] = sheet["name"]; row["instance_id"] = sheet["name"]
                    row["table"] = row.get("Table"); row["address"] = row.get("Equipment address")
                    row["channel_offset"] = row.get("Channel offset"); row["member_name"] = _member_from_var(row.get("GVL_COM variable") or row.get("DUT_COM member"))
                    row["data_type"] = row.get("Type"); row["direction"] = row.get("Direction")
                    state["rtu_channels"].append(row)
    elif filename == "IO_PHYSICAL.xlsx":
        state["io_modules"] = [{**r, "id": str(r.get("Slot", "")), "slot": r.get("Slot"),
                                 "module": r.get("Module"), "device": r.get("CODESYS device"), "status": r.get("Status")} for r in _objects(book, "RACK")]
        state["io_channels"] = []
        for r in _objects(book, "IO"):
            slot = str(r.get("Slot", "")); module = str(r.get("Module", "")); channel = str(r.get("Channel", ""))
            state["io_channels"].append({**r, "id": f"{slot}:{module}:{channel}", "module_id": slot,
                                          "slot": r.get("Slot"), "channel": r.get("Channel"),
                                          "instance_id": r.get("Instance"), "variable": r.get("GVL_IOMAP variable"),
                                          "member_name": _member_from_var(r.get("GVL_IO member")),
                                          "signal": r.get("Signal"), "io_type": r.get("I/O type")})
    elif filename == "PARAMETERS_PA.xlsx":
        state["pa_values"] = []
        for sheet in book.get("sheets", []):
            if sheet["name"] not in {"README", "LISTS"}:
                for row in _objects(book, sheet["name"]):
                    row = dict(row); row["id"] = f"{sheet['name']}:{row.get('Member')}"
                    row["instance"] = sheet["name"]; row["instance_id"] = sheet["name"]
                    row["member_name"] = row.get("Member"); row["data_type"] = row.get("Type")
                    row["default_value"] = row.get("Default value"); row["value"] = row.get("Instance value")
                    row["supervision_editable"] = row.get("Changed from supervision")
                    state["pa_values"].append(row)
    elif filename == "EXCHANGES_EXC.xlsx":
        state["exchange_functions"] = [{**r, "id": str(r.get("FC", "")), "name": r.get("FC"),
                                         "type_a_id": r.get("Object type A"), "type_b_id": r.get("Object type B"),
                                         "parameter_a": r.get("Parameter A"), "parameter_b": r.get("Parameter B"),
                                         "sheet": r.get("Sheet")} for r in _objects(book, "FC_EXC")]
        state["exchange_calls"] = [{**r, "id": f"{r.get('FC')}:{r.get('Instance A')}:{r.get('Instance B')}",
                                     "function_id": r.get("FC"), "instance_a_id": r.get("Instance A"),
                                     "instance_b_id": r.get("Instance B"), "parameter_a_value": r.get("Parameter A value"),
                                     "parameter_b_value": r.get("Parameter B value")} for r in _objects(book, "CALLS")]
        state["exchange_functions_detail"] = []
        for sheet in book.get("sheets", []):
            if sheet["name"] not in {"README", "FC_EXC", "CALLS", "LISTS"}:
                for row in _objects(book, sheet["name"]):
                    row = dict(row); row["id"] = f"{sheet['name']}:{row.get('Source')}:{row.get('Destination')}"
                    row["sheet"] = sheet["name"]; row["exchange_function_id"] = sheet["name"]
                    row["source"] = row.get("Source"); row["destination"] = row.get("Destination")
                    row["source_member"] = str(row.get("Source", "")).split(".")[-1]
                    row["destination_member"] = str(row.get("Destination", "")).split(".")[-1]
                    row["direction"] = row.get("Direction"); row["data_type"] = row.get("Type")
                    state["exchange_functions_detail"].append(row)


def _instances(state: dict, snapshot: Any = None) -> list[dict]:
    type_by_name = {str(t["name"]): t for t in state.get("types", [])}
    out, seen = [], set()
    for slave in state.get("rtu_slaves", []):
        name = slave.get("Instance") or slave.get("instance")
        kind = slave.get("Object type") or slave.get("object_type")
        typ = type_by_name.get(str(kind))
        if name and str(name) not in seen:
            out.append({"id": str(name), "name": name, "type_id": typ["id"] if typ else str(kind or "")})
            seen.add(str(name))
    for point in state.get("tcp_points", []):
        name = point.get("Instance") or point.get("instance")
        typ_name = re.sub(r"_\d+$", "", str(name or ""))
        typ = type_by_name.get(typ_name)
        if name and typ and str(name) not in seen:
            out.append({"id": str(name), "name": name, "type_id": typ["id"]})
            seen.add(str(name))
    # The XML snapshot may expose object instances in typed GVL variables.
    if snapshot is not None:
        xml = snapshot.decode("utf-8", "ignore") if isinstance(snapshot, bytes) else str(snapshot)
        try:
            root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(xml)
            for node in root.iter():
                name = node.attrib.get("name", "")
                match = re.fullmatch(r"(?:COM|EXC|IO|PA)_([A-Z][A-Z0-9_]*?)(?:_\d+)?", name)
                if not match:
                    continue
                candidate = match.group(1)
                # Strip an instance suffix only when it yields a known type.
                typ_name = candidate
                inst_name = candidate
                if re.fullmatch(r".+_\d+", candidate) and candidate.rsplit("_", 1)[0] in type_by_name:
                    typ_name = candidate.rsplit("_", 1)[0]
                elif candidate in type_by_name and "UPI" not in str(type_by_name[candidate].get("kind", "")).upper():
                    continue
                typ = type_by_name.get(typ_name)
                if typ and inst_name not in seen:
                    out.append({"id": inst_name, "name": inst_name, "type_id": typ["id"]})
                    seen.add(inst_name)
        except Exception:
            pass
    return out


@functools.lru_cache(maxsize=2)
def load_pack(root: Path = DEFAULT_PACK) -> dict:
    """Load the flattened v2 reference pack into a normalized, lossless state."""
    root = Path(root)
    state = {"workbooks": {}, "types": [], "instances": [], "tcp_clients": [], "tcp_points": [],
             "rtu_lines": [], "rtu_slaves": [], "rtu_channels": [], "io_modules": [], "io_channels": [],
             "pa_values": [], "exchange_functions": [], "exchange_calls": [], "exchange_functions_detail": [],
             "functions": [], "mapped_variables": [], "snapshot": None, "fuxa": {}}
    for path in sorted(root.rglob("*.xlsx")):
        if path.name.startswith("~$") or "templates" in {p.lower() for p in path.parts}:
            continue
        book = workbook_to_data(path.read_bytes(), path.name)
        state["workbooks"][path.name] = book
    state["types"] = [_normalize_type(state["workbooks"][f]) for f in sorted(TYPE_FILES) if f in state["workbooks"]]
    for filename in sorted(PROJECT_FILES):
        if filename in state["workbooks"]:
            _excel_state(state, state["workbooks"][filename])
    snap = root / "latest_codesys.xml"
    if snap.exists():
        state["snapshot"] = _snapshot_metadata(snap.read_text(encoding="utf-8", errors="replace"), snap.name)
    csv_file = next(iter(sorted(root.rglob("SCADA_V1-devices_v4.csv"))), None)
    if csv_file:
        original = csv_file.read_bytes()
        state["fuxa"] = {"filename": csv_file.name, "original_b64": base64.b64encode(original).decode("ascii"),
                          "sha256": hashlib.sha256(original).hexdigest(), "points": _parse_fuxa(original)}
    state["instances"] = _instances(state, state.get("snapshot", {}).get("xml") if state.get("snapshot") else None)
    _attach_fuxa_ids(state)
    state["fuxa"]["tcp_digest"] = _digest(state["tcp_points"])
    state["functions"] = [{"id": o["name"], "name": o["name"], "declaration": o.get("declaration", ""),
                            "body": o.get("body", ""), "custom_code": ""}
                           for o in state.get("snapshot", {}).get("objects", []) if o.get("name", "").startswith("FNC_")]
    for typ in state["types"]:
        pou = next((o for o in state.get("snapshot", {}).get("objects", []) if o.get("name") == typ["fb"].get("name")), None)
        if pou:
            typ["fb"].update({"declaration": pou.get("declaration", ""), "body": pou.get("body", "")})
    state["mapped_variables"] = _mapped_vars(state)
    return state


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _snapshot_metadata(xml: str, filename: str) -> dict:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    objects = []
    object_ids = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "Object":
            continue
        name, object_id = node.attrib.get("Name"), node.attrib.get("ObjectId")
        if name:
            object_ids[name] = object_id
            objects.append({"name": name, "id": object_id, "folder": "", "kind": ""})
    ns = "{http://www.plcopen.org/xml/tc6_0200}"
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local not in {"pou", "dataType", "globalVars"}:
            continue
        name = node.attrib.get("name")
        found = next((o for o in objects if o["name"] == name), None)
        if found:
            found["kind"] = {"pou": node.attrib.get("pouType", "POU"), "dataType": "DUT", "globalVars": "GVL"}[local]
            if local == "pou":
                decl = node.find(f"{ns}interface")
                body = node.find(f"{ns}body/{ns}ST/{{http://www.w3.org/1999/xhtml}}xhtml")
                found["declaration"] = "".join(decl.itertext()) if decl is not None else ""
                found["body"] = (body.text or "") if body is not None else ""
    project_structure = next((n for n in root.iter() if n.tag.rsplit("}", 1)[-1] == "ProjectStructure"), None)
    def folders(node, parents=()):
        for child in list(node):
            local = child.tag.rsplit("}", 1)[-1]
            if local == "Folder":
                yield from folders(child, parents + (child.attrib.get("Name", ""),))
            elif local == "Object":
                name = child.attrib.get("Name")
                target = next((o for o in objects if o["name"] == name), None)
                if target:
                    target["folder"] = "/".join(x for x in parents if x)
                yield from folders(child, parents)
    if project_structure is not None:
        list(folders(project_structure))
    header = root.find(f"{ns}fileHeader")
    content = root.find(f"{ns}contentHeader")
    return {"filename": filename, "xml": xml, "object_count": len(objects), "object_ids": object_ids, "objects": objects,
            "file_header": header.attrib.copy() if header is not None else {},
            "content_header": content.attrib.copy() if content is not None else {},
            "imported_at": datetime.now(timezone.utc).isoformat()}


def _parse_fuxa(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig")
    result = []
    for row in csv.reader(io.StringIO(text, newline="")):
        if row and row[0] == "T@":
            row += [""] * (21 - len(row))
            result.append({"device_id": row[1], "id": row[2], "name": row[3], "label": row[4],
                           "type": row[5], "memaddress": row[6], "address": row[7], "divisor": row[8],
                           "daq_enabled": row[15], "daq_interval": row[16], "daq_changed": row[17], "daq_restored": row[18]})
    return result


def _mapped_vars(state: dict) -> list[dict]:
    found = {}
    for book in state.get("workbooks", {}).values():
        for sheet in book.get("sheets", []):
            hs = [str(h or "").casefold() for h in sheet.get("headers", [])]
            for col in ("gvl_modbus variable", "gvl_iomap variable"):
                if col in hs:
                    i = hs.index(col)
                    for row in sheet.get("rows", []):
                        value = row[i] if i < len(row) else None
                        if value:
                            found[str(value)] = {"name": str(value), "source_file": book.get("filename"), "sheet": sheet["name"]}
    return list(found.values())


def _attach_fuxa_ids(state: dict) -> None:
    """Attach reference tag IDs to the corresponding FUXA TCP points."""
    fuxa_points = [p for p in state.get("fuxa", {}).get("points", []) if str(p.get("device_id")) != "0"]
    prefixes = {"coils": "000000", "discrete_inputs": "100000",
                "input_registers": "300000", "holding_registers": "400000"}
    for point in state.get("tcp_points", []):
        if str(point.get("Client", "")) != "FUXA":
            continue
        table = str(point.get("table", ""))
        address = point.get("Client address")
        if address is None:
            address = point.get("Modbus address")
        if table == "discrete_inputs" and address not in (None, ""):
            address = int(address) - (100000 if int(address) >= 100000 else 10000)
        candidates = [p for p in fuxa_points if p.get("memaddress") == prefixes.get(table) and str(p.get("address")) == str(address)]
        match = next((p for p in candidates if str(point.get("Name", "")).casefold() == str(p.get("name", "")).casefold()), None)
        if match:
            point["FUXA id"] = match["id"]


def parse_project(workbooks: dict, snapshot: Any = None, fuxa_csv: bytes | None = None) -> dict:
    """Normalize an arbitrary set of already-decoded workbook payloads."""
    state = {"workbooks": copy.deepcopy(workbooks), "types": [], "instances": [], "tcp_clients": [],
             "tcp_points": [], "rtu_lines": [], "rtu_slaves": [], "rtu_channels": [], "io_modules": [],
             "io_channels": [], "pa_values": [], "exchange_functions": [], "exchange_calls": [],
             "exchange_functions_detail": [], "functions": [], "mapped_variables": [], "snapshot": snapshot, "fuxa": {}}
    for book in workbooks.values():
        if book.get("filename") in TYPE_FILES:
            state["types"].append(_normalize_type(book))
        else:
            _excel_state(state, book)
    if isinstance(snapshot, str):
        try: snapshot = _snapshot_metadata(snapshot, "snapshot.xml")
        except Exception: snapshot = {"xml": snapshot, "objects": [], "object_ids": {}}
    if fuxa_csv is not None:
        state["fuxa"] = {"original_b64": base64.b64encode(fuxa_csv).decode(),
                         "sha256": hashlib.sha256(fuxa_csv).hexdigest(), "points": _parse_fuxa(fuxa_csv)}
    state["instances"] = _instances(state, snapshot.get("xml") if isinstance(snapshot, dict) else snapshot)
    _attach_fuxa_ids(state)
    for typ in state["types"]:
        pou = next((o for o in (snapshot.get("objects", []) if isinstance(snapshot, dict) else [])
                    if o.get("name") == typ["fb"].get("name")), None)
        if pou:
            typ["fb"].update({"declaration": pou.get("declaration", ""), "body": pou.get("body", "")})
    state["mapped_variables"] = _mapped_vars(state)
    state["functions"] = [{"id": o["name"], "name": o["name"], "declaration": o.get("declaration", ""),
                            "body": o.get("body", ""), "custom_code": ""}
                           for o in (snapshot.get("objects", []) if isinstance(snapshot, dict) else []) if o.get("name", "").startswith("FNC_")]
    return state


def _key_for(sheet_name: str, row: dict) -> tuple:
    priority = {
        "TYPE": ("Key",), "CLIENTS": ("Client",), "LINES": ("Line",), "SLAVES": ("Instance",),
        "RACK": ("Slot",), "IO": ("Slot", "Module", "Channel"),
        "FC_EXC": ("FC",), "CALLS": ("FC", "Instance A", "Instance B"),
        "RTU_DEFAULT": ("Key",),
    }
    cols = priority.get(sheet_name)
    if cols and any(row.get(c) is not None for c in cols):
        return tuple(row.get(c) for c in cols)
    if sheet_name.startswith("DUT_") and row.get("Member"):
        return (row.get("Member"),)
    if sheet_name in {"COILS", "DISCRETE_INPUTS", "INPUT_REGISTERS", "HOLDING_REGISTERS"}:
        return (row.get("Modbus address"), row.get("Client"))
    if "Equipment address" in row:
        return (row.get("Table"), row.get("Equipment address")) if row.get("Table") is not None else (row.get("Equipment address"),)
    if all(row.get(c) is not None for c in ("Direction", "Source", "Destination")):
        return (row.get("Direction"), row.get("Source"), row.get("Destination"))
    if row.get("Key") is not None:
        return (row.get("Key"),)
    if "Member" in row:
        return (row.get("Member"),)
    return tuple(row.values()) if any(v is not None and v != "" for v in row.values()) else None


def _rows_diff(before: dict, after: dict) -> dict:
    added, modified, removed = [], [], []
    a, b = _sheet_map(before), _sheet_map(after)
    for name in list(dict.fromkeys([*a, *b])):
        sa, sb = a.get(name, {"headers": [], "rows": []}), b.get(name, {"headers": [], "rows": []})
        headers = [str(x or "") for x in (sb.get("headers") or sa.get("headers") or [])]
        def index(sheet):
            out = {}
            for values in sheet.get("rows", []):
                row = dict(zip([str(x or "") for x in sheet.get("headers", [])], values))
                out[_key_for(name, row)] = row
            return out
        ia, ib = index(sa), index(sb)
        added.extend({"sheet": name, "key": k, "row": ib[k]} for k in ib.keys() - ia.keys())
        removed.extend({"sheet": name, "key": k, "row": ia[k]} for k in ia.keys() - ib.keys())
        modified.extend({"sheet": name, "key": k, "before": ia[k], "after": ib[k]}
                        for k in ia.keys() & ib.keys() if ia[k] != ib[k])
    return {"added": added, "modified": modified, "removed": removed}


def _merge_books(current: dict, incoming: dict, mode: str) -> dict:
    if mode == "replace":
        return copy.deepcopy(incoming)
    old_sheets = _sheet_map(current)
    new_sheets = _sheet_map(incoming)
    # A merge updates matching natural keys while retaining unuploaded rows,
    # columns, and sheets from the current workbook.
    sheets = []
    names = list(dict.fromkeys([*(s["name"] for s in current.get("sheets", [])),
                                *(s["name"] for s in incoming.get("sheets", []))]))
    for name in names:
        old, new = old_sheets.get(name), new_sheets.get(name)
        if new is None:
            sheets.append(copy.deepcopy(old)); continue
        if old is None:
            sheets.append(copy.deepcopy(new)); continue
        # README and LISTS are workbook metadata, not data tables. Their rows
        # are position-sensitive (and often contain blank cells), so treating
        # them as natural-key rows creates duplicates on an unchanged upload.
        if name in {"README", "LISTS"}:
            sheets.append(copy.deepcopy(new)); continue
        old_headers = [str(h or "") for h in old.get("headers", [])]
        new_headers = [str(h or "") for h in new.get("headers", [])]
        headers = list(dict.fromkeys([*new_headers, *(h for h in old_headers if h not in new_headers)]))
        old_positions: dict[tuple, list[int]] = {}
        merged_rows: list[list[Any]] = []
        for pos, values in enumerate(old.get("rows", [])):
            old_row = dict(zip(old_headers, values))
            merged_rows.append([old_row.get(h) for h in headers])
            key = _key_for(name, old_row)
            if key is not None:
                old_positions.setdefault(key, []).append(pos)
        consumed: dict[tuple, int] = {}
        for values in new.get("rows", []):
            incoming_row = dict(zip(new_headers, values))
            key = _key_for(name, incoming_row)
            matched_pos = None
            if key is not None and key in old_positions:
                used = consumed.get(key, 0)
                if used < len(old_positions[key]):
                    matched_pos = old_positions[key][used]
                    consumed[key] = used + 1
            if matched_pos is None:
                merged_rows.append([incoming_row.get(h) for h in headers])
            else:
                old_row = dict(zip(headers, merged_rows[matched_pos]))
                old_row.update(incoming_row)
                merged_rows[matched_pos] = [old_row.get(h) for h in headers]
        sheets.append({**copy.deepcopy(new), "headers": headers, "rows": merged_rows})
    return {**copy.deepcopy(current), "filename": incoming["filename"], "sheets": sheets}


def validate_project(state: dict) -> dict:
    """Validate cross references and resource collisions across the full project."""
    return _validate(state, "")


def _validate(state: dict, filename: str) -> dict:
    errors, warnings = [], []
    types = {str(t.get("name")): t for t in state.get("types", [])}
    instances = {str(i.get("name")): i for i in state.get("instances", [])}
    type_ids = {t.get("id") for t in state.get("types", [])}
    known_types = {str(value) for value in type_ids} | set(types)
    for instance in state.get("instances", []):
        if instance.get("type_id") not in known_types:
            errors.append(f"Instance {instance.get('name')} references unknown object type {instance.get('type_id')}")
    if filename in ("MODBUS_TCP.xlsx", ""):
        by_table = {}
        occupied = {}
        clients = {str(c.get("Client")): c for c in state.get("tcp_clients", [])}
        ranges = {}
        for client, row in clients.items():
            ranges[client] = row.get("Address range")
        for row in state.get("tcp_points", []):
            address = row.get("Modbus address")
            table = row.get("table")
            client = str(row.get("Client", ""))
            client_address = row.get("Client address")
            address_key = int(address) if str(address).isdigit() else str(address)
            key = (table, address_key)
            if key[1] not in (None, "") and key in occupied:
                errors.append(f"Duplicate TCP Modbus address {key[1]} in {table} (clients {occupied[key]} and {client})")
            occupied[key] = client
            by_table[(table, client, address)] = row
            if client and client not in clients:
                errors.append(f"TCP point references unknown client {client}")
            inst = row.get("Instance")
            if inst and str(inst) not in instances:
                errors.append(f"TCP point references unknown instance {inst}")
            if address not in (None, "") and client in clients:
                text_range = str(ranges.get(client) or "")
                range_labels = {"coils": "coils", "discrete_inputs": "di", "input_registers": "ir", "holding_registers": "hr"}
                label = range_labels.get(str(table), "")
                match = re.search(rf"\b{label}\s+(\d+)\s*[-–]\s*(\d+)\b", text_range, re.I) if label else None
                if not match and "+1000" in text_range.replace(" ", ""):
                    base = str(ranges.get("FUXA") or "")
                    match = re.search(rf"\b{label}\s+(\d+)\s*[-–]\s*(\d+)\b", base, re.I) if label else None
                    if match:
                        lo, hi = int(match.group(1)) + 1000, int(match.group(2)) + 1000
                        match = None
                    else:
                        lo = hi = None
                else:
                    lo = hi = None
                if match:
                    range_value = address if client == "FUXA" else client_address
                    numeric = int(range_value) if str(range_value).isdigit() else None
                    lo, hi = int(match.group(1)), int(match.group(2))
                    if numeric is not None and not lo <= numeric <= hi:
                        errors.append(f"TCP address {address} is outside {client} {label} range {lo}-{hi}")
                elif lo is not None and hi is not None:
                    numeric = int(client_address) if str(client_address).isdigit() else None
                    if numeric is not None and not lo <= numeric <= hi:
                        errors.append(f"TCP address {address} is outside {client} {label} range {lo}-{hi}")
            member_name = row.get("member_name")
            inst = instances.get(str(inst)) if inst else None
            if member_name and inst:
                typ = types.get(str(inst.get("type_id")))
                com = next((d for d in typ.get("duts", []) if d.get("role") == "COM"), None) if typ else None
                if com and member_name not in {str(m.get("name")) for m in com.get("members", [])}:
                    errors.append(f"TCP point member is missing from DUT_COM: {inst.get('name')}.{member_name}")
    if filename in ("MODBUS_RTU.xlsx", ""):
        slave_names = {str(r.get("Instance")) for r in state.get("rtu_slaves", [])}
        line_names = {str(r.get("Line")) for r in state.get("rtu_lines", [])}
        addresses = {}
        for slave in state.get("rtu_slaves", []):
            instance = str(slave.get("Instance", "")); typ = str(slave.get("Object type", ""))
            if typ and typ not in types:
                errors.append(f"RTU slave {instance} references unknown type {typ}")
            if instance and instance not in instances:
                errors.append(f"RTU slave references unknown instance {instance}")
            line = str(slave.get("Line", ""))
            if line and line not in line_names:
                errors.append(f"RTU slave {instance} references unknown line {line}")
            key = (str(slave.get("Line", "")), str(slave.get("Slave address", "")))
            if key[1] not in ("", "TO CHECK", "None") and key in addresses:
                errors.append(f"Duplicate RTU slave address {key[1]} on line {key[0]}")
            if key[1] not in ("", "TO CHECK", "None"):
                try:
                    numeric = int(str(key[1]), 0) if str(key[1]).lower().startswith("0x") else int(key[1])
                    if not 1 <= numeric <= 247:
                        errors.append(f"RTU slave address out of range for {instance}: {key[1]} (expected 1-247)")
                    if typ == "DIP1500" and not 1 <= numeric <= 31:
                        errors.append(f"DIP1500 slave address out of range for {instance}: {key[1]} (expected 1-31)")
                except (TypeError, ValueError):
                    errors.append(f"Invalid RTU slave address for {instance}: {key[1]}")
            addresses[key] = instance
        for channel in state.get("rtu_channels", []):
            if channel.get("instance") not in slave_names:
                errors.append(f"RTU channel instance missing from SLAVES: {channel.get('instance')}")
            inst = instances.get(str(channel.get("instance")))
            typ = types.get(str(inst.get("type_id"))) if inst else None
            com = next((d for d in typ.get("duts", []) if d.get("role") == "COM"), None) if typ else None
            member_name = channel.get("member_name")
            if com and member_name and member_name not in {str(m.get("name")) for m in com.get("members", [])}:
                errors.append(f"RTU channel member is missing from DUT_COM: {channel.get('instance')}.{member_name}")
    if filename in ("IO_PHYSICAL.xlsx", ""):
        seen = set()
        module_slots = {str(r.get("Slot")) for r in state.get("io_modules", [])}
        for row in state.get("io_channels", []):
            key = (row.get("Slot"), row.get("Module"), row.get("Channel"))
            if key in seen:
                errors.append(f"I/O channel assigned more than once: {key}")
            seen.add(key)
            slot = str(row.get("Slot", ""))
            if slot and slot not in module_slots:
                errors.append(f"I/O channel references unknown rack slot {slot}")
            inst = row.get("Instance") or row.get("instance_id")
            if inst and str(inst) not in instances:
                errors.append(f"I/O channel references unknown instance {inst}")
    for t in state.get("types", []):
        for dut in t.get("duts", []):
            member_names = [m.get("name") for m in dut.get("members", [])]
            if len(member_names) != len(set(member_names)):
                errors.append(f"Duplicate member name in {t.get('name')}/{dut.get('name')}")
    if filename in ("EXCHANGES_EXC.xlsx", ""):
        declared_fcs = {str(f.get("id", f.get("FC", ""))) for f in state.get("exchange_functions", [])}
        for function in state.get("exchange_functions", []):
            for field in ("Object type A", "Object type B", "type_a_id", "type_b_id"):
                value = function.get(field)
                if value and str(value) not in known_types:
                    errors.append(f"Exchange function {function.get('id', function.get('FC'))} references unknown object type {value}")
        for call in state.get("exchange_calls", []):
            function_id = call.get("function_id", call.get("FC"))
            if function_id and str(function_id) not in declared_fcs:
                errors.append(f"Exchange call references undeclared FC_EXC: {function_id}")
            for field in ("Instance A", "Instance B", "instance_a_id", "instance_b_id"):
                value = call.get(field)
                if value and str(value) not in instances:
                    errors.append(f"Exchange call references unknown instance {value}")
        for exchange in state.get("exchange_functions_detail", []):
            for side, key, kind in (("source", "source_member", "type_a_id"), ("destination", "destination_member", "type_b_id")):
                member = exchange.get(key)
                if not member:
                    continue
                typename = str(exchange.get("sheet", "")).split("-", 1)[0 if side == "source" else -1]
                typ = types.get(typename)
                dut = next((d for d in typ.get("duts", []) if d.get("role") == "EXC"), None) if typ else None
                if dut and member not in {str(m.get("name")) for m in dut.get("members", [])}:
                    errors.append(f"Exchange {side} member missing from DUT_EXC {typename}: {member}")
    for key, value in state.get("workbooks", {}).items():
        if filename and value.get("filename") != filename: continue
        for sheet in value.get("sheets", []):
            hs = [str(h or "") for h in sheet.get("headers", [])]
            if sheet["name"] not in {"README", "LISTS"} and not hs and sheet.get("rows"):
                errors.append(f"Missing headers in {value.get('filename')}/{sheet['name']}")
    # Unknown and uncertain workbook values are deliberately surfaced as warnings.
    for book in state.get("workbooks", {}).values():
        if filename and book.get("filename") != filename: continue
        for sheet in book.get("sheets", []):
            if sheet["name"] in {"README", "LISTS"}:
                continue
            for row in sheet.get("rows", []):
                if any(isinstance(v, str) and "TO CHECK" in v.upper() for v in row):
                    warnings.append(f"Unconfirmed TO CHECK value in {book.get('filename')}/{sheet['name']}")
                    break
    if not filename or filename == "MODBUS_TCP.xlsx":
        def mem_for_table(table):
            return {"coils": "000000", "discrete_inputs": "100000", "input_registers": "300000", "holding_registers": "400000"}.get(table)
        represented = set()
        for row in state.get("tcp_points", []):
            table = row.get("table")
            addr = row.get("Modbus address")
            if table == "discrete_inputs" and addr not in (None, ""):
                addr = int(addr) - 10000
            represented.add((mem_for_table(table), str(addr), str(row.get("Name", "")).casefold()))
        for point in state.get("fuxa", {}).get("points", []):
            if str(point.get("device_id")) == "0":
                continue
            key = (str(point.get("memaddress", "")), str(point.get("address", "")), str(point.get("name", "")).casefold())
            if key not in represented:
                warnings.append(f"Retained FUXA tag is not modeled in MODBUS_TCP: {point.get('name')}")
    return {"errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}


def import_workbook(data: bytes, filename: str, current: dict, mode: str = "merge") -> dict:
    """Preview an upload; never mutate the supplied state."""
    safe = Path(filename).name
    is_type_file = bool(re.fullmatch(r"TYPE_[A-Z0-9_]+\.xlsx", filename))
    if safe != filename or filename.startswith(".") or Path(filename).suffix.lower() in UNSAFE_SUFFIXES or (filename not in PROJECT_FILES and not is_type_file):
        return {"state": copy.deepcopy(current), "diff": {"added": [], "modified": [], "removed": []},
                "validation": {"errors": ["Unsafe or unsupported workbook filename"], "warnings": []}}
    try:
        incoming = workbook_to_data(data, filename)
    except (ValueError, zipfile.BadZipFile, OSError, KeyError) as exc:
        return {"state": copy.deepcopy(current), "diff": {"added": [], "modified": [], "removed": []},
                "validation": {"errors": [f"Invalid workbook: {exc}"], "warnings": []}}
    # Any structured sheet with rows must have non-empty, unique headers.
    bad_headers = [s["name"] for s in incoming["sheets"] if s.get("rows") and s["name"] not in {"README", "LISTS"} and
                   (not s.get("headers") or any(not h for h in s["headers"]) or len(s["headers"]) != len(set(s["headers"])))]
    if bad_headers:
        return {"state": copy.deepcopy(current), "diff": {"added": [], "modified": [], "removed": []},
                "validation": {"errors": [f"Missing or duplicate headers in {', '.join(bad_headers)}"], "warnings": []}}
    reference_book = load_pack().get("workbooks", {}).get(filename, {})
    expected_sheets = _sheet_map(reference_book)
    for incoming_sheet in incoming["sheets"]:
        if incoming_sheet["name"] in {"README", "LISTS"}:
            continue
        expected = expected_sheets.get(incoming_sheet["name"])
        if expected is None and filename in {"MODBUS_RTU.xlsx", "PARAMETERS_PA.xlsx", "EXCHANGES_EXC.xlsx"}:
            expected = next((x for n, x in expected_sheets.items() if n not in {"README", "LISTS", "LINES", "SLAVES", "FC_EXC", "CALLS"}), None)
        if expected:
            missing = [h for h in expected.get("headers", []) if h not in incoming_sheet.get("headers", [])]
            if missing:
                return {"state": copy.deepcopy(current), "diff": {"added": [], "modified": [], "removed": []},
                        "validation": {"errors": [f"Missing required headers in {filename}/{incoming_sheet['name']}: {', '.join(map(str, missing))}"], "warnings": []}}
    previous = copy.deepcopy(current.get("workbooks", {}).get(filename, {"filename": filename, "sheets": []}))
    changed = _merge_books(previous, incoming, "replace" if mode == "replace" else "merge")
    new_state = copy.deepcopy(current)
    new_state.setdefault("workbooks", {})[filename] = changed
    previous_fuxa = copy.deepcopy(current.get("fuxa", {}))
    project = parse_project(new_state["workbooks"], new_state.get("snapshot"),
                              base64.b64decode(previous_fuxa["original_b64"]) if previous_fuxa.get("original_b64") else None)
    project["fuxa"] = previous_fuxa
    old_types = {str(t.get("id")): t for t in current.get("types", [])}
    for typ in project.get("types", []):
        old = old_types.get(str(typ.get("id")))
        if old and old.get("fb"):
            typ["fb"].update({k: copy.deepcopy(old["fb"].get(k, typ["fb"].get(k)))
                              for k in ("declaration", "body", "custom_code")})
    old_functions = {str(f.get("id", f.get("name"))): f for f in current.get("functions", [])}
    for function in project.get("functions", []):
        old = old_functions.get(str(function.get("id", function.get("name"))))
        if old:
            function.update({k: copy.deepcopy(old.get(k, function.get(k))) for k in ("declaration", "body", "custom_code")})
    # Preserve migration/source metadata unknown to the Excel-normalized model.
    model_keys = set(project)
    for key, value in new_state.items():
        if key not in model_keys:
            project[key] = copy.deepcopy(value)
    validation = _validate(project, "")
    for sheet in changed.get("sheets", []):
        if sheet["name"] not in {"README", "LISTS"} and sheet.get("rows"):
            headers = sheet.get("headers", [])
            if not headers or any(h in (None, "") for h in headers) or len(headers) != len(set(headers)):
                validation["errors"].append(f"Missing or duplicate headers in {filename}/{sheet['name']}")
    return {"state": project, "diff": _rows_diff(previous, changed), "validation": validation}


def _updated_lists(state: dict, source: dict) -> dict:
    """Refresh the project's three entity dropdown columns in LISTS."""
    result = copy.deepcopy(source)
    headers = [str(value or "") for value in result.get("headers", [])]
    columns = {name: index for index, name in enumerate(headers)
               if name in {"OBJ_TYPE", "INSTANCE", "FC_EXC"}}
    desired = {
        "OBJ_TYPE": [str(item.get("name") or item.get("id")) for item in state.get("types", [])
                     if item.get("name") or item.get("id")],
        "INSTANCE": [str(item.get("name") or item.get("id")) for item in state.get("instances", [])
                     if item.get("name") or item.get("id")],
        "FC_EXC": [str(item.get("name") or item.get("id")) for item in state.get("exchange_functions", [])
                   if item.get("name") or item.get("id")],
    }
    rows = result.setdefault("rows", [])
    for header, col in columns.items():
        existing = [row[col] for row in rows if col < len(row) and row[col] not in (None, "")]
        # Preserve intentional template choices such as <INSTANCE> while using
        # the current model as the authoritative list of concrete entities.
        placeholders = [value for value in existing if isinstance(value, str) and value.startswith("<") and value.endswith(">")]
        wanted = set(desired[header])
        # Keep the workbook's established ordering, drop deleted entities, and
        # append newly introduced entities in the model's order.
        concrete = [value for value in existing if value in wanted]
        concrete.extend(value for value in desired[header] if value not in concrete)
        values = list(dict.fromkeys([*concrete, *placeholders]))
        needed_rows = max(len(rows), len(values))
        while len(rows) < needed_rows:
            rows.append([None] * len(headers))
        for row in rows:
            if len(row) < len(headers):
                row.extend([None] * (len(headers) - len(row)))
        for index, row in enumerate(rows):
            row[col] = values[index] if index < len(values) else None
    return result


def export_workbook(state: dict, filename: str) -> bytes:
    """Write a state workbook into its official template, preserving workbook features."""
    safe = Path(filename).name
    if safe != filename or not safe.lower().endswith(".xlsx"):
        raise ValueError("Unsafe or unsupported workbook filename")
    book = state.get("workbooks", {}).get(filename)
    if not book:
        raise ValueError(f"Workbook is not present in project state: {filename}")
    template_name = {"TYPE_": "TEMPLATE_TYPE.xlsx", "MODBUS_TCP.xlsx": "TEMPLATE_MODBUS_TCP.xlsx",
                     "MODBUS_RTU.xlsx": "TEMPLATE_MODBUS_RTU.xlsx", "IO_PHYSICAL.xlsx": "TEMPLATE_IO_PHYSICAL.xlsx",
                     "PARAMETERS_PA.xlsx": "TEMPLATE_PARAMETERS_PA.xlsx", "EXCHANGES_EXC.xlsx": "TEMPLATE_EXCHANGES_EXC.xlsx"}
    template_file = template_name.get(filename, "TEMPLATE_TYPE.xlsx") if filename.startswith("TYPE_") else template_name.get(filename)
    template = DEFAULT_PACK / "02_excel" / "templates" / template_file if template_file else Path()
    if not template.exists():
        template = DEFAULT_PACK / "02_excel" / filename
    if not template.exists():
        raise FileNotFoundError(f"Official workbook template missing for {filename}")
    wb = openpyxl.load_workbook(template)
    sheets = _sheet_map(book)
    # Project workbooks carry one tab per RTU slave, parameterized equipment,
    # or exchange function. Clone the matching official placeholder sheet.
    prototypes = {"MODBUS_RTU.xlsx": "<INSTANCE>", "PARAMETERS_PA.xlsx": "<INSTANCE>",
                  "EXCHANGES_EXC.xlsx": "<A>-<B>"}
    prototype_name = prototypes.get(filename)
    prototype = wb[prototype_name] if prototype_name and prototype_name in wb.sheetnames else None
    for source_name in sheets:
        if source_name in wb.sheetnames or source_name in {"README", "LISTS"}:
            continue
        if prototype is None:
            continue
        new_ws = wb.copy_worksheet(prototype)
        new_ws.title = source_name
    if prototype_name:
        for ws in list(wb.worksheets):
            if ws.title == prototype_name and prototype_name not in sheets:
                wb.remove(ws)
    # The exported tab order follows the source workbook exactly.
    wb._sheets = [wb[name] for name in sheets if name in wb.sheetnames]
    for ws in wb.worksheets:
        source = sheets.get(ws.title)
        if source is None:
            continue
        if ws.title == "LISTS":
            source = _updated_lists(state, source)
        headers = list(source.get("headers", []))
        template_headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        if ws.title in {"README", "LISTS"}:
            target_columns = {str(value): idx for idx, value in enumerate(headers, 1) if value is not None}
        else:
            target_columns = {str(value): idx for idx, value in enumerate(headers, 1) if value is not None}
            template_columns = {str(value): idx for idx, value in enumerate(template_headers, 1) if value not in (None, "")}
            # Carry each official column's comments, styles, and validations
            # with its header when an uploaded sheet changes column order.
            template_details = {}
            for header, old_col in template_columns.items():
                template_details[header] = [(ws.cell(r, old_col)._style,
                                              copy.copy(ws.cell(r, old_col).comment),
                                              ws.cell(r, old_col).number_format)
                                             for r in range(1, max(2, ws.max_row) + 1)]
            for header, new_col in target_columns.items():
                old_col = template_columns.get(header)
                if old_col is None:
                    old_col = max(template_columns.values(), default=1)
                    header = str(header)
                details = template_details.get(str(header))
                for r in range(1, max(2, ws.max_row) + 1):
                    dst = ws.cell(r, new_col)
                    if details and r <= len(details):
                        style, comment, number_format = details[r - 1]
                        if style is not None:
                            dst._style = copy.copy(style)
                        dst.comment = copy.copy(comment)
                        dst.number_format = number_format
                    else:
                        src = ws.cell(min(r, max(2, ws.max_row)), old_col)
                        if src.has_style:
                            dst._style = copy.copy(src._style)
            if template_columns:
                for dv in ws.data_validations.dataValidation:
                    new_ranges = []
                    for cell_range in dv.sqref.ranges:
                        first_old = cell_range.min_col
                        column_header = next((h for h, c in template_columns.items() if c == first_old), None)
                        new_col = target_columns.get(column_header, first_old)
                        if cell_range.min_col == cell_range.max_col:
                            from openpyxl.utils import get_column_letter
                            new_ranges.append(f"{get_column_letter(new_col)}{cell_range.min_row}:{get_column_letter(new_col)}{cell_range.max_row}")
                        else:
                            new_ranges.append(str(cell_range))
                    dv.sqref = " ".join(new_ranges)
        for header in headers:
            if header is not None:
                ws.cell(1, target_columns[str(header)]).value = header
        for row_num in range(2, ws.max_row + 1):
            for col_num in range(1, ws.max_column + 1):
                ws.cell(row_num, col_num).value = None
        for r, row in enumerate(source.get("rows", []), 2):
            for c, value in enumerate(row, 1):
                if c <= len(headers) and headers[c - 1] is not None:
                    ws.cell(r, target_columns[str(headers[c - 1])]).value = value
    output = io.BytesIO(); wb.save(output)
    return output.getvalue()


def export_fuxa(state: dict) -> bytes:
    """Return the exact source CSV while unchanged; otherwise replace tag rows."""
    fuxa = state.get("fuxa", {})
    if fuxa.get("original_b64"):
        original = base64.b64decode(fuxa["original_b64"])
        if hashlib.sha256(original).hexdigest() == fuxa.get("sha256"):
            # If normalized TCP points still match those parsed at load time,
            # preserve all comments and device blocks byte for byte.
            if fuxa.get("points") == _parse_fuxa(original) and fuxa.get("tcp_digest") == _digest(state.get("tcp_points", [])):
                return original
    original = base64.b64decode(fuxa["original_b64"]) if fuxa.get("original_b64") else b""
    lines = original.decode("utf-8-sig").splitlines() if original else []
    if not lines:
        raise ValueError("No FUXA reference CSV is loaded")
    tag_start = next((i for i, line in enumerate(lines) if line.startswith("T@,")), len(lines))
    fixed = lines[:tag_start]
    system_tags = [line for line in lines[tag_start:] if line.startswith("T@,") and line.split(",", 3)[1] == "0"]
    system_end = max((i for i, line in enumerate(lines) if line in system_tags), default=tag_start - 1) + 1
    separator = []
    while system_end < len(lines) and not lines[system_end]:
        separator.append(""); system_end += 1
    tags = []
    existing = {str(p.get("id")): p for p in fuxa.get("points", [])}
    rows = [p for p in state.get("tcp_points", []) if str(p.get("Client", p.get("client", ""))) == "FUXA" and p.get("Name")]
    rows = [p for p in rows if not p.get("Word") or str(p.get("Word")).upper() == "LSW"]
    unique = {}
    for row in rows:
        key = (row.get("table"), row.get("Client address") or row.get("Modbus address"), row.get("Name"))
        unique.setdefault(key, row)
    rows = list(unique.values())
    for row in rows:
        table = row.get("table") or row.get("sheet", "").lower()
        table = str(table).lower()
        prefix = {"coils": "000000", "discrete_inputs": "100000", "input_registers": "300000", "holding_registers": "400000"}.get(table)
        if not prefix: continue
        address = row.get("Client address") or row.get("Modbus address") or ""
        if table == "discrete_inputs": address = int(address) - 100000 if int(address) > 10000 else int(address)
        ident = row.get("FUXA id") or ""
        prior = existing.get(str(ident), {})
        if not prior:
            relative_address = int(address) - 10000 if table == "discrete_inputs" and int(address) >= 10000 else address
            prior = next((p for p in fuxa.get("points", []) if p.get("memaddress") == prefix and str(p.get("address")) == str(relative_address)), {})
        if not ident:
            ident = prior.get("id") or ""
        if not re.fullmatch(r"t_[0-9a-f]{8}-[0-9a-f]{8}", str(ident)):
            import uuid
            ident = prior.get("id") or f"t_{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[8:16]}"
        data_type = row.get("FUXA type") or {"BOOL": "Bool", "REAL": "Float32MLE", "WORD": "UInt16"}.get(str(row.get("Type", "")).upper(), "")
        name = str(row.get("Name", ""))
        if any(c in name for c in ',~"\r\n') or name != name.strip():
            raise ValueError(f"Unsafe FUXA tag name: {name!r}")
        tags.append(f"T@,{prior.get('device_id') or 'd_abeaaea7-b8914e5b'},{ident},{name},,{data_type},{prefix},{address},1,,,,,,,,false,60,false,false,")
    # Retain existing tag order and IDs, including the two unmodeled test tags
    # in the supplied FUXA file. Newly-created project points follow afterward.
    def tag_key(line: str) -> tuple | None:
        fields = line.split(",")
        if len(fields) < 8 or fields[0] != "T@" or fields[1] == "0":
            return None
        return (fields[6], str(fields[7]), fields[3].casefold())
    generated = {tag_key(line): line for line in tags if tag_key(line) is not None}
    original_by_id = {line.split(",")[2]: line for line in lines[tag_start:] if line.startswith("T@,") and len(line.split(",")) > 3}
    ordered_tags, used = [], set()
    for point in fuxa.get("points", []):
        if str(point.get("device_id")) == "0":
            continue
        key = (str(point.get("memaddress", "")), str(point.get("address", "")), str(point.get("name", "")).casefold())
        line = generated.get(key)
        if line:
            prior_line = original_by_id.get(point.get("id"))
            if prior_line:
                old_fields, new_fields = prior_line.split(","), line.split(",")
                if len(old_fields) == len(new_fields) and old_fields[3].casefold() == new_fields[3].casefold():
                    old_cmp, new_cmp = old_fields.copy(), new_fields.copy()
                    old_cmp[3] = new_cmp[3] = old_cmp[3].casefold()
                    if old_cmp == new_cmp:
                        line = prior_line
            ordered_tags.append(line); used.add(key)
        elif point.get("id") in original_by_id:
            ordered_tags.append(original_by_id[point["id"]])
    ordered_tags.extend(line for key, line in generated.items() if key not in used)
    joined = "\n".join([*fixed, *system_tags, *separator, *ordered_tags]).rstrip("\n") + "\n\n\n"
    return joined.encode("utf-8")

