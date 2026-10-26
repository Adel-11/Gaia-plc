"""Reviewed creation of new PLC object types."""
from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel

IEC_RESERVED = {"ACTION", "ARRAY", "AT", "BOOL", "CASE", "CONFIGURATION", "CONSTANT", "DATE", "DINT", "ELSE", "ELSIF", "END_ACTION", "END_CASE", "END_CONFIGURATION", "END_FOR", "END_FUNCTION", "END_FUNCTION_BLOCK", "END_IF", "END_PROGRAM", "END_REPEAT", "END_RESOURCE", "END_STRUCT", "END_TYPE", "END_VAR", "END_WHILE", "EXIT", "FALSE", "FOR", "FUNCTION", "FUNCTION_BLOCK", "IF", "INT", "LREAL", "PROGRAM", "REAL", "REPEAT", "RESOURCE", "RETURN", "STRING", "STRUCT", "TIME", "TRUE", "TYPE", "UDINT", "UINT", "VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP", "VAR_EXTERNAL", "WHILE", "WORD"}
ROLES = {"Equipment": ["PA", "COM", "EXC", "IO", "INT"], "Functional unit (UPI)": ["COM", "EXC", "INT"]}
IEC_SCALARS = {"BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "REAL", "LREAL", "TIME", "DATE", "TIME_OF_DAY", "DATE_AND_TIME", "STRING", "WSTRING"}


class TypeCreate(BaseModel):
    revision: int
    name: str
    kind: str
    description: str = ""
    communication: str = ""
    folder: str = ""
    copy_from: str | None = None
    standard_modes_from: str | None = None
    duts: list[dict] | None = None
    body: str | None = None
    processing_code: str | None = None
    custom_code: str | None = None
    rtu_default: dict | None = None
    rtu: dict | None = None
    supervision: list | None = None
    upi_bindings: list[dict] | None = None


class ApplyTypeCreate(BaseModel):
    token: str


def _identifier(value):
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or ""))) and str(value).upper() not in IEC_RESERVED


def _iec_type_error(value, known_types):
    text = re.sub(r"\s+", "", str(value or ""))
    if re.fullmatch(r"(?:W?STRING)\(\d+\)", text, re.I):
        return None
    array = re.fullmatch(r"ARRAY\[(-?\d+)\.\.(-?\d+)\]OF([A-Za-z_][A-Za-z0-9_]*)", text, re.I)
    if array:
        if int(array.group(1)) > int(array.group(2)):
            return f"Invalid array bounds in IEC type {value!r}."
        text = array.group(3)
    if text.upper() in IEC_SCALARS or text in known_types or text in {"TON", "TOF", "TP", "R_TRIG", "F_TRIG", "CTU", "CTD", "CTUD", "DUT_Union_Real_Word"}:
        return None
    return f"Unknown IEC type {value!r}."


def _type_validation_closure(root_type, existing_types):
    """Select the root type and only existing types needed by its DUT members."""
    selected = [root_type]
    available = {str(dut.get("name")): candidate
                 for candidate in existing_types
                 for dut in candidate.get("duts", []) if dut.get("name")}
    scanned = set()
    referenced_names = set()
    while len(scanned) < len(selected):
        candidate = next(item for item in selected if id(item) not in scanned)
        scanned.add(id(candidate))
        for dut in candidate.get("duts", []):
            for member in dut.get("members", []):
                ref = re.sub(r"^ARRAY\s*\[[^]]+\]\s*OF\s*", "", str(member.get("iec_type", "")), flags=re.I).strip()
                dependency = available.get(ref)
                if dependency is not None and dependency not in selected:
                    selected.append(dependency)
                    referenced_names.update(str(item.get("name")) for item in dependency.get("duts", []) if item.get("name"))
    return selected, referenced_names


def _replace_st_identifiers(text, mapping):
    """Rename identifier tokens in code while leaving IEC comments and strings intact."""
    pattern = re.compile(r"\(\*.*?\*\)|//[^\r\n]*|'(?:(?:'')|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z0-9_]*", re.S)
    def sub(match):
        token = match.group(0)
        if token.startswith(("(*", "//", "'", '"')):
            return token
        return mapping.get(token, token)
    return pattern.sub(sub, text or "")


def _template_book(engine, filename):
    template = engine.DEFAULT_PACK / "02_excel" / "templates" / "TEMPLATE_TYPE.xlsx"
    return engine.workbook_to_data(template.read_bytes(), filename)


def _book_for_type(engine, typ, filename):
    book = _template_book(engine, filename)
    sm = {s["name"]: s for s in book["sheets"]}
    def set_pairs(sheet, pairs):
        sh = sm[sheet]
        index = {str(h): i for i, h in enumerate(sh["headers"])}
        sh["rows"] = [[None] * len(sh["headers"]) for _ in pairs]
        for row, (key, value) in zip(sh["rows"], pairs):
            row[index["Key"]], row[index["Value"]] = key, value
    metadata = [("Object type", typ["name"]), ("Kind", typ["kind"]), ("Description", typ.get("description", "")),
                ("Communication", typ.get("communication", "")), ("Project folder", typ.get("folder", "")),
                ("Function block", typ["fb"]["name"])]
    for dut in typ["duts"]:
        metadata.append(("DUT_" + dut["role"], dut["name"]))
        sh = sm["DUT_" + dut["role"]]
        ix = {str(h): i for i, h in enumerate(sh["headers"])}
        rows = []
        for m in dut.get("members", []):
            row = [None] * len(sh["headers"])
            for key, value in (("Section", m.get("section")), ("Member", m.get("name")), ("Type", m.get("iec_type")), ("Default value", m.get("default_value")), ("Unit", m.get("unit")), ("Comment", m.get("comment")), ("Exchange", m.get("exchange"))):
                row[ix[key]] = value
            rows.append(row)
        sh["rows"] = rows
    set_pairs("TYPE", metadata)
    set_pairs("RTU_DEFAULT", (typ.get("rtu_default") or {}).items())
    for sheet in ("RTU_COILS", "RTU_DI", "RTU_IR", "RTU_HR"):
        sh = sm[sheet]
        headers = sh["headers"]
        sh["rows"] = [[r.get(h) for h in headers] for r in (typ.get("rtu") or {}).get(sheet, [])]
    for sheet in ("TEMPLATE_PA", "TEMPLATE_RTU", "TEMPLATE_EXC", "TEMPLATE_TCP", "TEMPLATE_IO"):
        sh = sm[sheet]
        headers = sh["headers"]
        sh["rows"] = [[row.get(h) for h in headers] for row in (typ.get("templates") or {}).get(sheet, [])]
    return book


def _sync_type_dut_sheet(state, typ, role):
    """Refresh one DUT sheet without disturbing other workbook content."""
    from backend import v2_engine as engine
    filename = str(typ.get("source_file") or f"TYPE_{typ.get('name')}.xlsx")
    workbooks = state.setdefault("workbooks", {})
    book = workbooks.get(filename)
    if book is None:
        book = _book_for_type(engine, typ, filename)
        workbooks[filename] = book
    sheet_name = f"DUT_{role}"
    sheet = engine._sheet_map(book).get(sheet_name)
    if sheet is None:
        generated = engine._sheet_map(_book_for_type(engine, typ, filename)).get(sheet_name)
        if generated is None:
            return
        sheet = copy.deepcopy(generated)
        book.setdefault("sheets", []).append(sheet)
    headers = [str(h or "") for h in sheet.get("headers", [])]
    indexes = {header: index for index, header in enumerate(headers)}
    members = next((d.get("members", []) for d in typ.get("duts", []) if d.get("role") == role), [])
    sheet["rows"] = []
    for member in sorted(members, key=lambda item: item.get("position", 0)):
        row = [None] * len(headers)
        values = {"Section": member.get("section"), "Member": member.get("name"), "Type": member.get("iec_type"),
                  "Default value": member.get("default_value"), "Unit": member.get("unit"),
                  "Comment": member.get("comment"), "Exchange": member.get("exchange")}
        for key, value in values.items():
            if key in indexes:
                row[indexes[key]] = value
        sheet["rows"].append(row)


def _pa_templates(typ):
    rows = []
    for dut in typ.get("duts", []):
        if dut.get("role") != "PA": continue
        for member in dut.get("members", []):
            if str(member.get("name", "")).upper() == "SPARE": continue
            rows.append({"Member": member.get("name"), "Type": member.get("iec_type"),
                         "Default value": member.get("default_value"), "Instance value": None,
                         "Unit": member.get("unit"), "Changed from supervision": "Yes",
                         "Comment": member.get("comment"),
                         "GVL_PA variable": f"GVL_PA.PA_<INSTANCE>.{member.get('name')}"})
    return rows


def _tcp_templates(typ, selections):
    roles = {d.get("role"): d for d in typ.get("duts", [])}
    rows, errors = [], []
    for selected in selections:
        ref = selected if isinstance(selected, str) else (selected.get("member_name") or selected.get("member") or selected.get("name") or "")
        bits = str(ref).split(".", 1)
        if len(bits) != 2 or bits[0] != "COM":
            errors.append(f"Supervision point {ref!r} must reference a COM member."); continue
        member = next((m for m in roles.get("COM", {}).get("members", []) if m.get("name") == bits[1]), None)
        if member is None:
            errors.append(f"Supervision member {ref!r} does not exist."); continue
        member_name = str(member["name"])
        client = "HMI" if member_name.upper().startswith("HMI_") else "FUXA" if member_name.upper().startswith("SCADA_") else "WEBVISU" if member_name.upper().startswith("WEBVISU_") else None
        if client is None:
            errors.append(f"Supervision member {ref!r} must start with SCADA_, HMI_, or WEBVISU_."); continue
        direction = "Client to PLC" if "_INPUT_" in member_name.upper() else "PLC to Client"
        iec = str(member.get("iec_type") or "BOOL").upper()
        word_value = iec == "DUT_UNION_REAL_WORD"
        if iec not in {"BOOL", "WORD", "DUT_UNION_REAL_WORD"}:
            errors.append(f"Supervision member {ref!r} has unsupported Modbus type {iec}; use BOOL, WORD, or DUT_Union_Real_Word."); continue
        table = ("HOLDING_REGISTERS" if direction == "Client to PLC" else "INPUT_REGISTERS") if word_value or iec == "WORD" else ("COILS" if direction == "Client to PLC" else "DISCRETE_INPUTS")
        base = {"Modbus address": None, "Client address": None, "Client": client, "Instance": "<INSTANCE>",
                "Name": member_name.replace("_", " ").title(), "Direction": direction,
                "Type": "REAL" if word_value else iec, "Word": None, "Scale": "1", "Unit": member.get("unit"),
                "GVL_MODBUS variable": None, "GVL_COM variable": f"GVL_COM.COM_<INSTANCE>.{member_name}",
                "Comment": f"Paste in {table}"}
        if word_value:
            for word, offset in (("LSW", 0), ("MSW", 1)):
                row = dict(base, Word=word, **{"GVL_COM variable": f"GVL_COM.COM_<INSTANCE>.{member_name}.Word_value[{offset}]"})
                rows.append(row)
        else:
            rows.append(base)
    return rows, errors


def _rtu_templates(typ):
    tables = {"RTU_COILS": ("Coil", "Coils"), "RTU_DI": ("Discrete input", "DiscreteInputs"),
              "RTU_IR": ("IR", "InputRegisters"), "RTU_HR": ("HR", "HoldingRegisters")}
    rows = []
    for sheet, (table, suffix) in tables.items():
        for source in (typ.get("rtu") or {}).get(sheet, []):
            member = source.get("DUT_COM member") or ""
            offset = source.get("Channel offset")
            rows.append({"Table": table, "Equipment address": source.get("Equipment address"),
                         "Channel offset": offset, "Function": source.get("Function"),
                         "Direction": source.get("Direction"), "Name": source.get("Name"),
                         "Type": source.get("Type"), "Word": source.get("Word"),
                         "Scale": source.get("Scale"), "Unit": source.get("Unit"),
                         "GVL_MODBUS variable": f"GVL_MODBUS.{typ['name']}_{suffix}[{offset}]" if offset is not None else None,
                         "GVL_COM variable": f"GVL_COM.COM_<INSTANCE>.{member}" if member else None,
                         "CODESYS channel": source.get("CODESYS channel"), "Comment": source.get("Comment")})
    return rows


def _prepare_upi_bindings(state, equipment, requested):
    """Validate explicit EXC transfers and materialize any named EXC leaves."""
    errors, bindings = [], []
    if not requested:
        return errors, bindings
    if equipment.get("kind") != "Equipment":
        return ["UPI bindings can only be defined for Equipment types."], bindings

    from backend.v2_fb_flow import variable_options
    from backend.v2_exchanges import validate_mappings, _sync as sync_exchanges

    def exc_dut(typ):
        return next((d for d in typ.get("duts", []) if d.get("role") == "EXC"), None)

    def leaves(typ):
        dut = exc_dut(typ)
        return {o["path"].removeprefix("EXC."): o["iec_type"] for o in variable_options({"duts": [dut] if dut else []}, state)}

    def ensure_leaf(typ, path, iec_type):
        if path in leaves(typ):
            return True
        dut = exc_dut(typ)
        if not dut or not _identifier(path) or not iec_type:
            return False
        if any(str(m.get("name", "")).casefold() == path.casefold() for m in dut.get("members", [])):
            return False
        position = max((int(m.get("position", i)) for i, m in enumerate(dut.get("members", []))), default=-1) + 1
        dut.setdefault("members", []).append({"id": f"{typ.get('id', typ.get('name'))}:EXC:{position}", "position": position,
                                               "name": path, "iec_type": iec_type})
        return True

    modified_types = {}

    def add_leaf(typ, path, iec_type):
        added = ensure_leaf(typ, path, iec_type)
        if added:
            modified_types[str(typ.get("id", typ.get("name")))] = typ
        return added

    scalar_types = {"BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "REAL", "LREAL", "TIME", "DATE", "TIME_OF_DAY", "DATE_AND_TIME", "STRING", "WSTRING", "DUT_UNION_REAL_WORD"}
    derived_types = {str(t.get("name")) for t in state.get("types", [])}
    derived_types.update(str(d.get("name")) for t in state.get("types", []) for d in t.get("duts", []))
    derived_types.update(str(o.get("name")) for o in (state.get("snapshot") or {}).get("objects", []))

    def supported_type(value):
        text = re.sub(r"\s+", "", str(value or ""))
        array = re.fullmatch(r"ARRAY\[(-?\d+)\.\.(-?\d+)\]OF([A-Za-z_][A-Za-z0-9_]*)", text, re.I)
        if array:
            base = array.group(3).upper()
            return int(array.group(1)) <= int(array.group(2)) and (base in scalar_types or array.group(3) in derived_types)
        return text.upper() in scalar_types or text in derived_types

    for binding in requested:
        upi_ident = str(binding.get("upi_type_id") or "")
        upi = next((t for t in state.get("types", []) if str(t.get("id")) == upi_ident or str(t.get("name")) == upi_ident), None)
        if not upi or "UPI" not in str(upi.get("kind", "")).upper():
            errors.append(f"UPI binding type {upi_ident} was not found.")
            continue
        if exc_dut(upi) is None:
            errors.append(f"UPI type {upi.get('name')} has no DUT_EXC.")
            continue
        raw_mappings = binding.get("mappings") or []
        if not raw_mappings:
            errors.append(f"UPI binding to {upi.get('name')} needs at least one explicit member mapping.")
            continue
        planned = []
        for mapping in raw_mappings:
            if not isinstance(mapping, dict):
                errors.append("UPI binding mapping must be an object.")
                continue
            source, destination = str(mapping.get("source") or ""), str(mapping.get("destination") or "")
            source_prefix, destination_prefix = source.split(".", 1)[0] if "." in source else "", destination.split(".", 1)[0] if "." in destination else ""
            if {source_prefix, destination_prefix} != {"equipment", "upi"}:
                errors.append("UPI binding mappings must connect one equipment.MEMBER and one upi.MEMBER path.")
                continue
            source_member, destination_member = source.split(".", 1)[1], destination.split(".", 1)[1]
            source_side, destination_side = source_prefix, destination_prefix
            if "." in source_member or "." in destination_member:
                errors.append("UPI binding members must name direct DUT_EXC members.")
                continue
            source_type = leaves(equipment if source_side == "equipment" else upi).get(source_member)
            destination_type = leaves(equipment if destination_side == "equipment" else upi).get(destination_member)
            supplied_type = str(mapping.get("iec_type") or mapping.get("type") or "")
            if supplied_type and not supported_type(supplied_type):
                errors.append(f"Unsupported IEC type {supplied_type!r} for UPI binding members.")
                continue
            if not source_type and destination_type:
                source_type_obj = equipment if source_side == "equipment" else upi
                if add_leaf(source_type_obj, source_member, supplied_type or destination_type): source_type = leaves(source_type_obj).get(source_member)
            if not destination_type and source_type:
                destination_type_obj = equipment if destination_side == "equipment" else upi
                if add_leaf(destination_type_obj, destination_member, supplied_type or source_type): destination_type = leaves(destination_type_obj).get(destination_member)
            if not source_type and supplied_type:
                source_type_obj = equipment if source_side == "equipment" else upi
                if add_leaf(source_type_obj, source_member, supplied_type): source_type = leaves(source_type_obj).get(source_member)
            if not destination_type and supplied_type:
                destination_type_obj = equipment if destination_side == "equipment" else upi
                if add_leaf(destination_type_obj, destination_member, supplied_type): destination_type = leaves(destination_type_obj).get(destination_member)
            if not source_type:
                errors.append(f"{source_prefix.title()} EXC member {source_member} does not exist; supply an IEC type to create it.")
                continue
            if not destination_type:
                errors.append(f"{destination_prefix.title()} EXC member {destination_member} does not exist; supply an IEC type to create it.")
                continue
            if re.sub(r"\s+", "", str(source_type)).upper() != re.sub(r"\s+", "", str(destination_type)).upper():
                errors.append(f"Incompatible UPI binding types: {source} → {destination}.")
                continue
            planned.append({"source": source, "destination": destination})
        if not planned:
            continue
        function_name = f"FC_EXC_{equipment['name']}_{upi['name']}"
        fn = next((f for f in state.setdefault("exchange_functions", []) if str(f.get("name") or f.get("FC")) == function_name), None)
        if fn is None:
            fn = {"id": function_name, "name": function_name, "FC": function_name,
                  "type_a_id": equipment.get("id", equipment["name"]), "type_b_id": upi.get("id", upi["name"]),
                  "Object type A": equipment["name"], "Object type B": upi["name"],
                  "parameter_a": f"EXC_{equipment['name']}", "parameter_b": f"EXC_{upi['name']}",
                  "Parameter A": f"EXC_{equipment['name']}", "Parameter B": f"EXC_{upi['name']}",
                  "sheet": f"{equipment['name']}-{upi['name']}"[:31], "custom_code": "", "mappings": []}
            state["exchange_functions"].append(fn)
        elif str(fn.get("type_a_id")) not in {str(equipment.get("id")), str(equipment.get("name"))} or str(fn.get("type_b_id")) not in {str(upi.get("id")), str(upi.get("name"))}:
            errors.append(f"Exchange function name {function_name} is already used by different types.")
            continue
        parameter_a, parameter_b = fn.get("parameter_a") or fn.get("Parameter A"), fn.get("parameter_b") or fn.get("Parameter B")
        generated = []
        for mapping in planned:
            source_prefix, source_member = mapping["source"].split(".", 1)
            destination_prefix, destination_member = mapping["destination"].split(".", 1)
            source_parameter = parameter_a if source_prefix == "equipment" else parameter_b
            destination_parameter = parameter_a if destination_prefix == "equipment" else parameter_b
            generated.append({"source": f"{source_parameter}.{source_member}",
                              "destination": f"{destination_parameter}.{destination_member}"})
        existing_mappings = fn.get("mappings")
        if existing_mappings is None:
            from backend.v2_exchanges import _normal_mappings
            existing_mappings = _normal_mappings(state, fn)
        # A persisted binding may be rehydrated in another project that has
        # shared types but a project-local EXCHANGE workbook. Replaying it must
        # materialize the same function without duplicating its mappings.
        generated = [*copy.deepcopy(existing_mappings), *generated]
        unique_generated, seen_generated = [], set()
        for row in generated:
            key = (str(row.get("source") or ""), str(row.get("destination") or ""))
            if key not in seen_generated:
                seen_generated.add(key)
                unique_generated.append(row)
        generated = unique_generated
        try:
            validate_mappings(fn, generated, state)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        fn["mappings"] = generated
        bindings.append({"upi_type_id": upi.get("id", upi["name"]), "mappings": planned,
                         "exchange_function_id": function_name})
        template_row = {
            "FC": function_name, "Instance A": "<INSTANCE>", "Instance B": "<UPI_INSTANCE>",
            "Parameter A value": "GVL_EXC.EXC_<INSTANCE>", "Parameter B value": "GVL_EXC.EXC_<UPI_INSTANCE>",
            "Comment": f"Automatic UPI binding to {upi.get('name')}"}
        templates = equipment.setdefault("templates", {}).setdefault("TEMPLATE_EXC", [])
        if not any(str(row.get("FC")) == function_name for row in templates):
            templates.append(template_row)
    for typ in modified_types.values():
        if str(typ.get("id", typ.get("name"))) != str(equipment.get("id", equipment.get("name"))):
            _sync_type_dut_sheet(state, typ, "EXC")
    if state.get("exchange_functions"):
        sync_exchanges(state)
    return errors, bindings


def register_type_creation_routes(app, get_session, current_user, csrf):
    from backend import v2_engine as engine, v2_store as store, v2_plc as plc
    from backend.db import ImportPreview

    def project_for(session):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "The v2 project has not been initialized")
        return state

    def construct(current, request):
        errors, warnings = [], []
        if request.rtu or request.rtu_default or request.supervision or request.upi_bindings:
            warnings.append("Communication recipes are not created with a type; add RTU, TCP, I/O, or UPI configuration after the type exists.")
        if request.kind not in ROLES: errors.append("Kind must be Equipment or Functional unit (UPI).")
        if not _identifier(request.name): errors.append("Name must be a valid non-reserved IEC identifier.")
        if any(str(t.get("name", "")).casefold() == request.name.casefold() for t in current.get("types", [])):
            errors.append(f"Type {request.name} already exists (names are case-insensitive).")
        if errors: return None, errors, warnings
        src = next((t for t in current.get("types", []) if str(t.get("id")) == request.copy_from or str(t.get("name")) == request.copy_from), None) if request.copy_from else None
        if request.copy_from and src is None: return None, ["Source type for copy was not found."], warnings
        typ = copy.deepcopy(src) if src else {"duts": [], "rtu": {}, "rtu_default": {}, "templates": {}}
        old = str(src.get("name")) if src else ""
        fb_old = str((src or {}).get("fb", {}).get("name") or "")
        typ.update(id=request.name, name=request.name, kind=request.kind, description=request.description, communication=request.communication, folder=request.folder or (src or {}).get("folder", ""))
        # New types define an isolated PLC object. Communication recipes are
        # configured later through the dedicated project editors.
        for key in ("upi_bindings", "supervision"):
            typ.pop(key, None)
        typ["rtu"] = {k: [] for k in ("RTU_COILS", "RTU_DI", "RTU_IR", "RTU_HR")}
        typ["rtu_default"] = {}
        typ["templates"] = {}
        typ["source_file"] = f"TYPE_{request.name}.xlsx"
        typ["fb"] = copy.deepcopy(typ.get("fb") or {})
        typ["fb"]["name"] = f"{'FB_INST' if request.kind == 'Equipment' else 'FB'}_{request.name}"
        if not src:
            typ["duts"] = [{"id": f"{request.name}:DUT_{role}", "name": f"DUT_{role}_{request.name}", "role": role, "members": [{"id": f"{request.name}:{role}:0", "position": 0, "name": "SPARE", "iec_type": "BOOL"}]} for role in ROLES[request.kind]]
            # Keep headers adjacent: the flow parser accepts an exact ordered
            # section skeleton and preserves each header when body code changes.
            typ["fb"].update(declaration="", body="// INPUTS\n// PROCESSING\n// OUTPUTS\n", custom_code="")
            typ["rtu"] = {k: [] for k in ("RTU_COILS", "RTU_DI", "RTU_IR", "RTU_HR")}
            typ["rtu_default"] = {}
        mapping = {old: request.name, fb_old: typ["fb"]["name"]} if old else {}
        # Keep the role set consistent with the chosen IEC object kind. Copy
        # matching roles and add the required SPARE DUTs for any missing role.
        existing_by_role = {d.get("role"): d for d in typ.get("duts", [])}
        typ["duts"] = [existing_by_role.get(role, {"role": role, "members": [{"name": "SPARE", "iec_type": "BOOL"}]}) for role in ROLES[request.kind]]
        for dut in typ["duts"]:
            role = dut.get("role")
            dut["id"] = f"{request.name}:DUT_{role}"
            dut["name"] = f"DUT_{role}_{request.name}"
            for m in dut.get("members", []):
                m["id"] = f"{request.name}:{role}:{m.get('position',0)}"
        mapping.update({str(d.get("id", "")): d["name"] for d in typ["duts"]})
        if src:
            # Source DUT and FB names are identifier references in declarations and ST.
            targets_by_role = {d.get("role"): d for d in typ["duts"]}
            for old_d in src.get("duts", []):
                new_d = targets_by_role.get(old_d.get("role"))
                if new_d is None: continue
                mapping[str(old_d.get("name"))] = new_d["name"]
                for om, nm in zip(old_d.get("members", []), new_d.get("members", [])):
                    if om.get("name") and nm.get("name"):
                        mapping[str(om["name"])] = nm["name"]
            for dut in typ["duts"]:
                for member in dut.get("members", []):
                    value = str(member.get("iec_type") or "")
                    for a, b in mapping.items(): value = re.sub(rf"\b{re.escape(a)}\b", b, value)
                    member["iec_type"] = value
            typ["fb"]["body"] = _replace_st_identifiers(typ["fb"].get("body", ""), mapping)
            typ["fb"]["custom_code"] = _replace_st_identifiers(typ["fb"].get("custom_code", ""), mapping)
            # Preserve the source FB's complete interface (including local
            # timers/edge detectors) when the PLC generator supports it.
            try:
                from lxml import etree
                source_objects = plc.build_program(current)
                source_fb = source_objects.get(fb_old, {}).get("element")
                source_interface = source_fb.find(plc.NS + "interface") if source_fb is not None else None
                if source_interface is not None:
                    cloned = copy.deepcopy(source_interface)
                    allowed_roles = set(ROLES[request.kind])
                    for group in list(cloned):
                        if etree.QName(group).localname not in {"inOutVars", "inputVars", "outputVars"}: continue
                        for variable in list(group):
                            if etree.QName(variable).localname == "variable" and variable.get("name") in {"PA", "COM", "EXC", "IO", "INT"} and variable.get("name") not in allowed_roles:
                                group.remove(variable)
                    for derived in cloned.iter(plc.NS + "derived"):
                        if derived.get("name") in mapping: derived.set("name", mapping[derived.get("name")])
                    typ["fb"]["interface_xml"] = etree.tostring(cloned, encoding="unicode")
            except Exception:
                warnings.append("The source FB interface could not be copied; generated standard interface will be used.")
            for tab in (typ.get("rtu") or {}).values():
                for row in tab:
                    for key, value in list(row.items()):
                        if isinstance(value, str): row[key] = _replace_st_identifiers(value, mapping)
        if request.duts is not None:
            for provided in request.duts:
                target = next((d for d in typ["duts"] if d["role"] == provided.get("role")), None)
                if target is None: errors.append(f"Unsupported DUT role: {provided.get('role')}"); continue
                target["members"] = copy.deepcopy(provided.get("members", []))
                for i, m in enumerate(target["members"]):
                    m.update(position=i, id=f"{request.name}:{target['role']}:{i}")
        if request.body is not None: typ["fb"]["body"] = request.body
        if request.custom_code is not None: typ["fb"]["custom_code"] = request.custom_code
        # Do not synthesize PA/RTU/TCP/IO/EXC instance recipes during type creation.
        typ["templates"] = {}
        if not any(d.get("role") == "INT" for d in typ["duts"]):
            typ["duts"].append({"id": f"{request.name}:DUT_INT", "name": f"DUT_INT_{request.name}", "role": "INT", "members": [{"id": f"{request.name}:INT:0", "position": 0, "name": "SPARE", "iec_type": "BOOL"}]})
        known_types = {str(name) for existing in current.get("types", [])
                       for name in [existing.get("name"), *(dut.get("name") for dut in existing.get("duts", []))] if name}
        known_types.update(str(d.get("name")) for d in typ["duts"] if d.get("name"))
        for d in typ["duts"]:
            for m in d.get("members", []):
                if not _identifier(m.get("name")): errors.append(f"Invalid member name in {d['name']}: {m.get('name')}")
                type_error = _iec_type_error(m.get("iec_type"), known_types)
                if type_error: errors.append(f"{d['name']}.{m.get('name')}: {type_error}")
        state = copy.deepcopy(current)
        state["types"].append(typ)
        state.setdefault("workbooks", {})[typ["source_file"]] = _book_for_type(engine, typ, typ["source_file"])
        # Re-normalize from the workbook so type Excel re-import is lossless.
        normalized = engine._normalize_type(state["workbooks"][typ["source_file"]])
        normalized["fb"].update(body=typ["fb"].get("body", ""), custom_code=typ["fb"].get("custom_code", ""))
        if typ["fb"].get("interface_xml"):
            normalized["fb"]["interface_xml"] = typ["fb"]["interface_xml"]
        normalized["templates"] = {}
        normalized.pop("upi_bindings", None)
        normalized["fb"]["body"] = typ["fb"].get("body", "")
        normalized["fb"]["custom_code"] = typ["fb"].get("custom_code", "")
        from backend.v2_fb_flow import ensure_flow
        ensure_flow(normalized["fb"])
        state["types"][-1] = normalized
        if request.processing_code is not None:
            from backend.v2_fb_flow import update_flow
            code = request.processing_code
            if code and not code.endswith(("\n", "\r")): code += "\n"
            flow_errors = update_flow(normalized, state, processing_code=code)
            errors.extend(flow_errors)
        state.setdefault("workbooks", {})[normalized["source_file"]] = _book_for_type(engine, normalized, normalized["source_file"])
        return state, errors, warnings

    @app.post("/api/v2/types/preview")
    def preview_type(request: TypeCreate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if request.revision != current.get("revision", 0): raise HTTPException(409, "Project revision changed; reload before creating a type")
        state, errors, warnings = construct(current, request)
        program_objects = {}
        if state and not errors:
            try:
                typ = state["types"][-1]
                validation_state = copy.deepcopy(state)
                selected_types, referenced_names = _type_validation_closure(typ, current.get("types", []))
                validation_state["types"] = selected_types
                validation_state["instances"] = []
                for collection in ("exchange_functions", "exchange_calls", "exchange_functions_detail",
                                  "tcp_points", "rtu_channels", "io_channels", "virtual_io"):
                    validation_state[collection] = []
                program_objects = plc.build_program(validation_state)
                xml = plc.serialize_program(program_objects, validation_state, list(program_objects))
                from backend.v2_plc_validation import validate_xml
                checked = validate_xml(xml, validation_state, program_objects)
                scoped_names = {str(typ.get("name")), str(typ.get("fb", {}).get("name")),
                                *(str(d.get("name")) for d in typ.get("duts", [])), *referenced_names}
                scoped_errors = [message for message in checked.get("errors", [])
                                 if any(name and name in str(message) for name in scoped_names)]
                errors.extend(scoped_errors)
                warnings.extend(message for message in checked.get("warnings", [])
                                if any(name and name in str(message) for name in scoped_names))
            except Exception as exc:
                errors.append(f"New type PLC blocks could not be built: {exc}")
        if errors: return {"token": None, "errors": errors, "warnings": warnings, "changes": [], "objects": [], "generated_st": ""}
        typ = state["types"][-1]
        changes = [f"Create type {typ['name']}", f"Create workbook {typ['source_file']}"]
        objects = [d["name"] for d in typ.get("duts", [])] + [typ["fb"]["name"]]
        token = secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token, owner_id=user.id, state_payload=json.dumps(state, ensure_ascii=False), diff_payload=json.dumps({"kind": "v2-type-create", "base_revision": current.get("revision", 0)}, ensure_ascii=False), expires_at=datetime.now(timezone.utc) + timedelta(minutes=20)))
        session.commit()
        preview_typ = typ
        dut_st = [f"TYPE {d['name']} : STRUCT\n" + "\n".join(f"    {m.get('name')} : {m.get('iec_type')};" for m in d.get("members", [])) + "\nEND_STRUCT\nEND_TYPE" for d in preview_typ.get("duts", [])]
        pou = program_objects.get(typ["fb"]["name"], {}).get("element")
        declarations = []
        if pou is not None:
            interface = pou.find(plc.NS + "interface")
            if interface is not None:
                from backend.v2_codesys import _st
                labels = {"inputVars": "VAR_INPUT", "outputVars": "VAR_OUTPUT", "inOutVars": "VAR_IN_OUT", "localVars": "VAR", "tempVars": "VAR_TEMP", "externalVars": "VAR_EXTERNAL"}
                for group in interface:
                    label = labels.get(group.tag.rsplit("}", 1)[-1])
                    if not label: continue
                    rendered = copy.deepcopy(group); rendered.tag = plc.NS + "globalVars"
                    declarations.append(_st(rendered).replace("VAR_GLOBAL", label, 1))
        fb_st = f"FUNCTION_BLOCK {typ['fb']['name']}\n" + "\n".join(declarations) + "\n" + (typ["fb"].get("body") or "") + "\nEND_FUNCTION_BLOCK"
        generated = "\n\n".join(dut_st + [fb_st])
        return {"token": token, "errors": errors, "warnings": warnings, "changes": changes, "objects": objects, "generated_st": generated}

    @app.post("/api/v2/types/apply")
    def apply_type(request: ApplyTypeCreate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, request.token)
        now = datetime.now(timezone.utc)
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview and preview.expires_at.tzinfo is None else (preview.expires_at if preview else now)
        if not preview or preview.owner_id != user.id or expiry <= now:
            raise HTTPException(404, "Type preview not found or expired")
        meta = json.loads(preview.diff_payload)
        if meta.get("kind") != "v2-type-create": raise HTTPException(404, "Type preview not found or expired")
        current = project_for(session)
        if current.get("revision", 0) != meta.get("base_revision"): raise HTTPException(409, "Project changed after preview; create a new preview")
        state = json.loads(preview.state_payload)
        session.info["type_blocks_only"] = True
        try:
            saved = store.save_project(session, state, username=getattr(user, "username", "admin"), expected_revision=current.get("revision", 0))
        finally:
            session.info.pop("type_blocks_only", None)
        session.delete(preview); session.commit()
        return {"revision": saved.get("revision"), "type": next(t for t in saved["types"] if t.get("source_file") and t["source_file"] not in current.get("workbooks", {}))}
