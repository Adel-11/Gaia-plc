"""PLCopen export planning and validation for the V2 generator."""
from __future__ import annotations

import copy
import hashlib
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
XSD_PATH = ROOT / "reference-v2" / "06_schema" / "tc6_xml_v201.xsd"
NS = "http://www.plcopen.org/xml/tc6_0200"
XHTML = "http://www.w3.org/1999/xhtml"
EXTERNAL_TYPES = {"DUT_Union_Real_Word", "TON", "TOF", "TP", "R_TRIG", "F_TRIG", "CTU", "CTD", "CTUD"}


def _local(node: etree._Element) -> str:
    return etree.QName(node).localname


def _name(element: etree._Element) -> str:
    return str(element.get("name") or element.get("Name") or "")


def _snapshot_xml(state: dict) -> bytes | None:
    snap = state.get("snapshot") or {}
    raw = snap.get("xml") if isinstance(snap, dict) else None
    return raw.encode("utf-8") if isinstance(raw, str) else raw if isinstance(raw, bytes) else None


def _snapshot_nodes(state: dict) -> dict[str, etree._Element]:
    raw = _snapshot_xml(state)
    if not raw:
        return {}
    try:
        root = etree.fromstring(raw)
    except etree.XMLSyntaxError:
        return {}
    found = {}
    for node in root.iter():
        if _local(node) in {"dataType", "globalVars", "pou"} and _name(node):
            found[_name(node)] = node
    return found


def _semantic(node: etree._Element):
    """Comparable PLCopen meaning, ignoring XML formatting and CODESYS IDs."""
    if _local(node) == "data" and node.get("name", "").endswith("/objectid"):
        return None
    attrs = tuple(sorted((k, _number(v) if k == "value" and _local(node) == "simpleValue" else v)
                         for k, v in node.attrib.items()))
    children = tuple(x for child in node if isinstance(child.tag, str) if (x := _semantic(child)) is not None)
    text = node.text or ""
    if _local(node) == "xhtml":
        text = _st_for_compare(text)
    else:
        text = " ".join(text.split())
    return (etree.QName(node).namespace, _local(node), attrs, text, children)


def _number(value: str) -> str:
    try:
        n = Decimal(value)
        return str(n.normalize())
    except InvalidOperation:
        return value


def _st_for_compare(text: str) -> str:
    # Compare lexical tokens so formatting changes vanish while literals remain exact.
    token_re = re.compile(r"(?P<block>\(\*.*?\*\))|(?P<line>//[^\r\n]*)|(?P<string>'(?:''|[^'])*')|(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<word>[A-Za-z_]\w*)|(?P<space>\s+)|(?P<operator>:=|<=|>=|<>|\*\*|\.\.|.)", re.S)
    tokens = []
    for match in token_re.finditer(text):
        kind, value = match.lastgroup, match.group()
        if kind in {"block", "line", "space"}:
            continue
        if kind == "number": value = _number(value)
        tokens.append(value)
    return "\x1f".join(tokens)


def _fingerprint(node: etree._Element) -> str:
    return hashlib.sha256(repr(_semantic(node)).encode("utf-8")).hexdigest()


def _changed(name: str, obj: dict, snapshots: dict[str, etree._Element]) -> bool:
    old = snapshots.get(name)
    element = obj.get("element")
    return old is None or not isinstance(element, etree._Element) or _fingerprint(old) != _fingerprint(element)


def _refs(obj: dict) -> set[str]:
    element = obj.get("element")
    if not isinstance(element, etree._Element):
        return set()
    refs = set()
    for node in element.iter():
        if _local(node) == "derived" and node.get("name"):
            refs.add(node.get("name"))
    # Generated ST is embedded as XHTML text. DUT names are also used in declarations.
    text = "\n".join(element.xpath(".//*[local-name()='ST']/*[local-name()='xhtml']/text()"))
    text = _st_code(text)
    refs.update(re.findall(r"\bDUT_[A-Za-z0-9_]+\b", text))
    refs.update(re.findall(r"\b((?:FC_EXC|FNC|GFC)_[A-Za-z0-9_]+)\s*\(", text))
    refs.update(re.findall(r"\b(GVL_[A-Za-z0-9_]+)\.", text))
    return refs


def _st_code(text: str) -> str:
    """Blank comments and string literals while retaining offsets/newlines."""
    pattern = re.compile(r"\(\*.*?\*\)|//[^\r\n]*|'(?:''|[^'])*'", re.S)
    return pattern.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group(0)), text)


def _object_index(objects: dict[str, dict]) -> dict[str, str]:
    return {str(value.get("name", key)): key for key, value in objects.items()}


def _focus_names(focus: str | None, state: dict, objects: dict[str, dict]) -> set[str]:
    if not focus:
        return set()
    kind, _, ident = focus.partition(":")
    if kind == "object":
        return {ident} if ident in objects else set()
    if kind == "type":
        typ = next((t for t in state.get("types", []) if str(t.get("id")) == ident), None)
        if not typ:
            return set()
        tname = str(typ.get("name", ident))
        names = {str(d.get("name")) for d in typ.get("duts", []) if d.get("name")}
        names.update(str(d.get("role")) + "_" + tname for d in typ.get("duts", []) if d.get("role"))
        if typ.get("fb", {}).get("name"):
            names.add(str(typ["fb"]["name"]))
        return names & set(_object_index(objects))
    if kind == "instance":
        inst = next((i for i in state.get("instances", []) if str(i.get("id")) == ident), None)
        if not inst:
            return set()
        iname = str(inst.get("name", ident))
        typ = next((t for t in state.get("types", []) if str(t.get("id")) == str(inst.get("type_id"))), {})
        is_upi = "upi" in str(typ.get("kind", "")).casefold()
        names = {"GVL_COM", "GVL_EXC", "GFB_UPI" if is_upi else "GFB_INST"}
        if not is_upi: names.update({"GVL_IO", "GVL_PA"})
        if any(p.get("instance_id") == ident or p.get("Instance") == iname for p in state.get("tcp_points", [])) or any(c.get("instance_id") == ident for c in state.get("rtu_channels", [])):
            names.update({"GFC_COM_IN", "GFC_COM_OUT"})
        if any(c.get("instance_id") == ident for c in state.get("io_channels", [])):
            names.update({"GFC_IO_IN", "GFC_IO_OUT"})
        if any(c.get("instance_a_id") == ident or c.get("instance_b_id") == ident for c in state.get("exchange_calls", [])):
            names.add("GFC_EXC")
        names.update(n for n in _object_index(objects) if iname in n)
        if typ:
            type_known = bool(typ.get("fb", {}).get("name") in (state.get("snapshot") or {}).get("object_ids", {}))
            if not type_known:
                names.update(str(d.get("name")) for d in typ.get("duts", []) if d.get("name"))
                if typ.get("fb", {}).get("name"): names.add(str(typ["fb"]["name"]))
        return names & set(_object_index(objects))
    # Backward-friendly object-name focus.
    return {focus} if focus in objects else set()


def _semantic_scope(names: set[str], objects: dict[str, dict], state: dict) -> set[str]:
    """Expand changed dependents for an explicitly focused type/instance."""
    out = set(names)
    allnames = set(objects)
    for target in list(names):
        if target.startswith("DUT_"):
            for typ in state.get("types", []):
                if any(str(d.get("name")) == target for d in typ.get("duts", [])) and typ.get("fb", {}).get("name"):
                    out.add(str(typ["fb"]["name"]))
    if any(n.startswith("FB_INST_") for n in names):
        out.add("GFB_INST")
    if any(n.startswith("FB_UPI_") for n in names):
        out.add("GFB_UPI")
    if any(n.startswith("FC_EXC_") for n in names):
        out.add("GFC_EXC")
    return out


def plan_export(state: dict, objects: dict[str, dict], mode: str = "full", focus: str | None = None,
                selected: list[str] | None = None) -> dict:
    """Return an explainable export plan; selected narrows the plan for preview."""
    snapshot = _snapshot_nodes(state)
    by_name = _object_index(objects)
    included: dict[str, dict] = {}
    warnings: list[str] = []
    errors: list[str] = []
    full = mode == "full"
    if mode not in {"full", "partial"}:
        errors.append(f"Unsupported export mode: {mode}")
    if full:
        candidates = {str(v.get("name", k)) for k, v in objects.items() if str(v.get("name", k)) != "DUT_Union_Real_Word"}
        reason = "full export"
    else:
        focused = _focus_names(focus, state, objects)
        scope = _semantic_scope(focused, objects, state)
        candidates = {n for n in scope if n in by_name and _changed(n, objects[by_name[n]], snapshot)}
        reason = "changed object required by focused item"
        if not focused:
            errors.append(f"Unknown or missing partial export focus: {focus!r}")
    # New or changed type dependencies must travel with dependent objects.
    changed = {str(v.get("name", k)) for k, v in objects.items() if _changed(str(v.get("name", k)), v, snapshot)}
    if full:
        candidates.update(changed)
    available_names = set(by_name)
    queue = list(candidates)
    while queue:
        current = queue.pop()
        key = by_name.get(current)
        if key is None:
            continue
        for dep in _refs(objects[key]):
            if dep in EXTERNAL_TYPES:
                continue
            if dep not in available_names:
                if dep not in snapshot:
                    errors.append(f"Object {current} references dependency {dep}, which is absent from both the generated program and the known CODESYS snapshot.")
                continue
            # Existing known types are external to this focused export only when unchanged.
            dep_obj = objects[by_name[dep]]
            if dep not in snapshot or _changed(dep, dep_obj, snapshot):
                if dep not in candidates:
                    candidates.add(dep); queue.append(dep)
    if selected is not None:
        chosen = set(selected)
        unknown = chosen - available_names
        if unknown:
            errors.extend(f"Selected object {name} is absent from generated program." for name in sorted(unknown))
        candidates = chosen & available_names
        if not candidates:
            errors.append("No objects selected for export.")
        # An explicit selection is only valid if its dependency closure is also selected or preserved.
        for name in list(candidates):
            key = by_name.get(name)
            if not key:
                continue
            for dep in _refs(objects[key]):
                if dep in EXTERNAL_TYPES or dep in snapshot:
                    continue
                if dep not in candidates:
                    suffix = "" if dep in available_names else " (not generated)"
                    errors.append(f"Selected export object {name} is missing required dependency {dep}{suffix}.")
    elif not full and not candidates and not errors:
        errors.append("No changed objects are required for this export focus.")
    for name in sorted(candidates):
        if name in (state.get('codesys_conflicts') or {}):
            errors.append(f'{name} has an unresolved CODESYS import difference; review the import before exporting this object.')
        if name not in by_name:
            errors.append(f"Planned object {name} is absent from generated program.")
            continue
        obj = objects[by_name[name]]
        inclusion_reason = 'full export' if full else ('focused object' if name in _focus_names(focus, state, objects) else ('selected manually' if selected is not None else reason))
        included[name] = {"name": name, "kind": obj.get("kind", "POU"), "reason": inclusion_reason}
    available = [{"name": str(v.get("name", k)), "kind": v.get("kind", "POU"), "changed": _changed(str(v.get("name", k)), v, snapshot)}
                 for k, v in objects.items() if str(v.get("name", k)) != "DUT_Union_Real_Word"]
    deletions = []
    old_names = {o.get("name") for o in (state.get("snapshot") or {}).get("objects", []) if isinstance(o, dict)
                 and str(o.get("kind", "")).casefold() in {"dut", "gvl", "pou", "function", "functionblock", "function_block", "program"}}
    deletions.extend(sorted(n for n in old_names if n and n not in available_names and n != "DUT_Union_Real_Word"))
    manual = []
    for table, needed, capacity in _tcp_capacity_overflow(state):
        manual.append(f"Increase the CODESYS Modbus TCP {table} server/device zone to at least {needed} addresses and update the generated array bounds.")
    current_map = {_mapped_variable_base(str(v.get("name"))) for v in state.get("mapped_variables", []) if v.get("name") and "INSTANCE" not in str(v.get("name")).upper() and "<" not in str(v.get("name"))}
    previous_map = {_mapped_variable_base(v) for v in _snapshot_mapped_variables(state)}
    for added in sorted(current_map - previous_map):
        manual.append(f"Map new variable {added} in the CODESYS device tree.")
    if any("trigger" in n.casefold() for n in current_map - previous_map):
        manual.append("Remap communication trigger variables in CODESYS.")
    if any((n.startswith("DUT_PA") or n == "GVL_PA") and _changed(n, objects[by_name[n]], snapshot) for n in included if n in by_name):
        warnings.append("GVL_PA or a DUT_PA is included; changed persistent declarations may reset values at the next download.")
    return {"included": list(included.values()), "available": available, "manual_deletions": deletions,
            "manual_actions": sorted(set(manual)), "warnings": warnings, "errors": errors}


def _highest_mapped_address(state: dict) -> int:
    highest = 0
    for point in state.get("tcp_points", []):
        try:
            address = int(point.get("Modbus address", point.get("address", 0)))
            table = str(point.get("table", point.get("sheet", ""))).casefold()
            if table == "discrete_inputs": address -= 10000
            elif table == "input_registers": address -= 30000 if address >= 30000 else 0
            elif table == "holding_registers": address -= 40000 if address >= 40000 else 0
            highest = max(highest, address)
        except (TypeError, ValueError):
            continue
    return highest


def _tcp_capacity_overflow(state: dict) -> list[tuple[str, int, int]]:
    requirements = {"coils": ("coils", 1504), "discrete_inputs": ("discrete inputs", 1504),
                    "input_registers": ("input registers", 1501), "holding_registers": ("holding registers", 1501)}
    highest = {key: 0 for key in requirements}
    for point in state.get("tcp_points", []):
        table = str(point.get("table", point.get("sheet", ""))).casefold()
        if table not in highest:
            continue
        try:
            address = int(point.get("Modbus address", point.get("address", 0)))
            if table == "discrete_inputs" and address >= 10000: address -= 10000
            if table == "input_registers" and address >= 30000: address -= 30000
            if table == "holding_registers" and address >= 40000: address -= 40000
            highest[table] = max(highest[table], address)
        except (TypeError, ValueError):
            continue
    return [(requirements[key][0], value, requirements[key][1]) for key, value in highest.items() if value > requirements[key][1]]


def _snapshot_mapped_variables(state: dict) -> set[str]:
    snap = state.get("snapshot") or {}
    result = set(map(str, snap.get("mapped_variables", []))) if isinstance(snap, dict) else set()
    for gvl in _snapshot_nodes(state).values():
        if _name(gvl) not in {"GVL_MODBUS", "GVL_IOMAP"}:
            continue
        for v in gvl.xpath("./*[local-name()='variable']/@name"):
            result.add(f"{_name(gvl)}.{v}")
    return result


def _mapped_variable_base(name: str) -> str:
    return re.sub(r"\[\d+\](?:\.\d+)?$", "", name)


def _schema() -> etree.XMLSchema:
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    # The supplied schema is 0201 while generated projects are 0200. Keeping
    # its prefixes intact and changing the namespace URI everywhere preserves
    # QName references inside the schema.
    raw = XSD_PATH.read_bytes().replace(b"tc6_0201", b"tc6_0200")
    return etree.XMLSchema(etree.fromstring(raw, parser))


def validate_xml(xmlbytes: bytes, state: dict, objects: dict[str, dict]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
        root = etree.fromstring(xmlbytes, parser)
    except (etree.XMLSyntaxError, TypeError) as exc:
        return {"errors": [f"Invalid XML: {exc}"], "warnings": []}
    try:
        schema = _schema()
        schema.assertValid(root)
    except (etree.XMLSchemaParseError, etree.DocumentInvalid, OSError) as exc:
        errors.append(f"PLCopen XML schema validation failed: {exc}")
    if etree.QName(root).namespace != NS:
        errors.append(f"Root namespace must be {NS}.")
    if any(_local(n) in {"Application", "Device", "task", "library"} for n in root.iter()):
        errors.append("Export must contain free objects only; Application, devices, tasks, and libraries are forbidden.")
    for xhtml in root.iter():
        if _local(xhtml) == "xhtml" and etree.QName(xhtml).namespace == XHTML and xhtml.nsmap.get(None) != XHTML:
            errors.append("ST and documentation must use XHTML as the default namespace, without an html: prefix.")
    object_ids: dict[str, str] = {}
    ids_by_name: dict[str, str] = {}
    object_names = set()
    types_node = root.find(f"{{{NS}}}types")
    if types_node is not None:
        for group in (types_node.find(f"{{{NS}}}dataTypes"), types_node.find(f"{{{NS}}}pous")):
            if group is not None:
                for child in group:
                    name = _name(child)
                    if name:
                        object_names.add(name)
                        oid = next((d.text for d in child.xpath(".//*[local-name()='ObjectId']") if d.text), None)
                        if oid:
                            ids_by_name[name] = oid
                            if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", oid):
                                errors.append(f"Object {name} has an invalid ObjectId UUID {oid}.")
                            if oid in object_ids:
                                errors.append(f"Duplicate ObjectId {oid} on {object_ids[oid]} and {name}.")
                            object_ids[oid] = name
    for gvl in root.xpath("//*[local-name()='globalVars']"):
        name = _name(gvl)
        if name:
            object_names.add(name)
            oid = next((d.text for d in gvl.xpath(".//*[local-name()='ObjectId']") if d.text), None)
            if oid:
                ids_by_name[name] = oid
                if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", oid):
                    errors.append(f"Object {name} has an invalid ObjectId UUID {oid}.")
                if oid in object_ids: errors.append(f"Duplicate ObjectId {oid} on {object_ids[oid]} and {name}.")
                object_ids[oid] = name
    structure = root.xpath("//*[local-name()='ProjectStructure']")
    if structure:
        struct_rows = [n for n in structure[0].xpath(".//*[local-name()='Object']") if n.get("Name")]
        struct_names = [n.get("Name") for n in struct_rows]
        for name in sorted(set(n for n in struct_names if struct_names.count(n) > 1)):
            errors.append(f"ProjectStructure contains object {name} more than once.")
        for row in struct_rows:
            name = row.get("Name")
            if name not in object_names:
                errors.append(f"ProjectStructure references object {name}, which is not exported.")
            struct_id = row.get("ObjectId")
            if not struct_id:
                errors.append(f"ProjectStructure object {name} has no ObjectId.")
            elif name in ids_by_name and struct_id != ids_by_name[name]:
                errors.append(f"ProjectStructure ObjectId for {name} does not match its object declaration.")
        for name in sorted(object_names - set(struct_names)):
            errors.append(f"Exported object {name} is missing from ProjectStructure.")
    for name in sorted(object_names - ids_by_name.keys()):
        errors.append(f"Exported object {name} has no ObjectId.")
    declared_gvls: dict[str, set[str]] = {}
    for gvl in root.xpath("//*[local-name()='globalVars']"):
        gname = _name(gvl)
        declared_gvls[gname] = {v.get("name") for v in gvl.xpath("./*[local-name()='variable']") if v.get("name")}
    for pou in root.xpath("//*[local-name()='pou']"):
        body = _st_code(pou.xpath("string(./*[local-name()='body']/*[local-name()='ST']/*[local-name()='xhtml'])"))
        decl = " ".join(pou.xpath(".//*[local-name()='derived']/@name"))
        # Conservative ST symbol scan: only qualified global references are required to resolve.
        for gvlname, member in re.findall(r"\b(GVL_[A-Za-z0-9_]+)\.([A-Za-z_][A-Za-z0-9_]*)", body):
            if gvlname in declared_gvls and member not in declared_gvls[gvlname]:
                errors.append(f"{_name(pou)} references missing member {gvlname}.{member}.")
            elif gvlname not in declared_gvls and not _snapshot_has_gvl(state, gvlname):
                errors.append(f"{_name(pou)} references {gvlname}, which is neither exported nor in the known CODESYS snapshot.")
        # Check user-defined DUT references against exported and preserved declarations.
        known_types = object_names | set(_snapshot_nodes(state)) | EXTERNAL_TYPES
        for type_name in set(re.findall(r"\bDUT_[A-Za-z0-9_]+\b", decl + " " + body)):
            if type_name not in known_types:
                errors.append(f"{_name(pou)} references undeclared type {type_name}.")
        known_objects = object_names | set(_snapshot_nodes(state)) | {str(o.get("name")) for o in (state.get("snapshot") or {}).get("objects", []) if o.get("name")}
        known_variables = set(pou.xpath(".//*[local-name()='interface']//*[local-name()='variable']/@name"))
        snap_pou = _snapshot_nodes(state).get(_name(pou))
        if snap_pou is not None and _local(snap_pou) == "pou":
            known_variables.update(snap_pou.xpath(".//*[local-name()='interface']//*[local-name()='variable']/@name"))
        known_variables.update(_known_instance_calls(state, objects))
        for call in set(re.findall(r"\b((?:FB|GFB|GFC|FC|FNC)_[A-Za-z0-9_]+)\s*\(", body)):
            if call not in known_objects and call not in known_variables and not (call.endswith("_1") and call[:-2] in known_objects):
                errors.append(f"{_name(pou)} calls missing function or function block {call}.")
    _check_dut_refs(root, state, object_names, errors)
    _check_member_references(root, state, errors, warnings)
    _check_modbus_bounds(state, root, errors)
    _check_mapping_changes(state, objects, root, warnings)
    _check_rtu_mapping_collisions(state, errors)
    return {"errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}


def _snapshot_has_gvl(state: dict, name: str) -> bool:
    return name in _snapshot_nodes(state)


def _known_instance_calls(state: dict, objects: dict[str, dict]) -> set[str]:
    result = set()
    types = {str(t.get("id")): t for t in state.get("types", [])}
    object_names = _object_index(objects)
    for inst in state.get("instances", []):
        iname = str(inst.get("name", inst.get("id", "")))
        typ = types.get(str(inst.get("type_id", "")), {})
        is_upi = "upi" in str(typ.get("kind", "")).casefold()
        result.add(f"FB_{iname}_1" if is_upi else f"FB_{iname}")
    result.update(f"{name}_1" for name in object_names if name in {"GFB_INST", "GFB_UPI"})
    return result


def _check_dut_refs(root: etree._Element, state: dict, object_names: set[str], errors: list[str]) -> None:
    known = object_names | set(_snapshot_nodes(state)) | {str(o.get("name")) for o in (state.get("snapshot") or {}).get("objects", []) if o.get("name")} | EXTERNAL_TYPES
    for node in root.xpath("//*[local-name()='derived']"):
        name = node.get("name")
        if name and (name.startswith("DUT_") or name.startswith("FB_")) and name not in known:
            errors.append(f"Missing declared type or function block {name}.")
    for node in root.xpath("//*[local-name()='array']/*[local-name()='dimension']"):
        try:
            low, high = int(node.get("lower")), int(node.get("upper"))
            if low > high: errors.append(f"Invalid array bounds {low}..{high}.")
        except (TypeError, ValueError):
            errors.append("Array dimension requires integer lower and upper bounds.")


def _check_member_references(root: etree._Element, state: dict, errors: list[str], warnings: list[str]) -> None:
    snapshot = _snapshot_nodes(state)
    program_nodes = {**snapshot}
    for node in root.xpath("//*[local-name()='globalVars' or local-name()='pou' or local-name()='dataType']"):
        name = _name(node)
        if name: program_nodes[name] = node
    type_nodes = {name: node for name, node in program_nodes.items() if _local(node) == "dataType"}

    def declared(node):
        return {v.get("name"): v for v in node.xpath("./*[local-name()='variable']") if v.get("name")}

    globals_by_name = {name: declared(node) for name, node in program_nodes.items() if _local(node) == "globalVars"}
    local_by_pou: dict[str, dict[str, etree._Element]] = {}
    for name, node in program_nodes.items():
        if _local(node) == "pou":
            local_by_pou[name] = {v.get("name"): v for v in node.xpath(".//*[local-name()='interface']//*[local-name()='variable']") if v.get("name")}

    def variable_type(var):
        typ = var.xpath("./*[local-name()='type']/*[1]")
        if not typ: return None, None
        current = typ[0]
        if _local(current) == "array":
            dims = current.xpath("./*[local-name()='dimension'][1]")
            bounds = None
            if dims:
                try: bounds = (int(dims[0].get("lower")), int(dims[0].get("upper")))
                except (TypeError, ValueError): pass
            base = current.xpath("./*[local-name()='baseType']/*[1]")
            return (base[0].get("name") if base and _local(base[0]) == "derived" else _local(base[0]) if base else None), bounds
        return (current.get("name") if _local(current) == "derived" else _local(current)), None

    def members(type_name):
        node = type_nodes.get(type_name or "")
        if node is None:
            if type_name == "DUT_Union_Real_Word": return {"Real_value": None, "Word_value": None}
            return None
        struct = node.xpath("./*[local-name()='baseType']/*[local-name()='struct']")
        if not struct: return None
        return {v.get("name"): v for v in struct[0].xpath("./*[local-name()='variable']") if v.get("name")}

    # Check qualified paths in ST, after comments and literals were blanked.
    for pou in root.xpath("//*[local-name()='pou']"):
        body = _st_code(pou.xpath("string(./*[local-name()='body']/*[local-name()='ST']/*[local-name()='xhtml'])"))
        for match in re.finditer(r"\b(GVL_[A-Za-z0-9_]+|EXC_[A-Za-z0-9_]+|COM|EXC|PA|IO|INTERN)((?:\.[A-Za-z0-9_]+(?:\[-?\d+\])?)+)", body):
            root_name = match.group(1)
            parts = match.group(2).lstrip(".").split(".")
            parts = [re.fullmatch(r"([A-Za-z0-9_]+)(?:\[(-?\d+)\])?", part).groups() for part in parts]
            type_name = None
            if root_name.startswith("GVL_"):
                scope = globals_by_name.get(root_name)
                if scope is None:
                    if root_name not in globals_by_name and root_name not in snapshot:
                        errors.append(f"{_name(pou)} references {root_name}, which is neither exported nor in the known CODESYS snapshot.")
                    continue
            else:
                local_scope = local_by_pou.get(_name(pou), {})
                local_var = local_scope.get(root_name)
                if local_var is None:
                    if root_name.startswith('EXC_'):
                        errors.append(f"{_name(pou)} references undeclared exchange parameter {root_name}.")
                    continue
                type_name, _ = variable_type(local_var)
                scope = None
            for pos, (member_name, index) in enumerate(parts):
                if scope is not None:
                    var = scope.get(member_name)
                    if var is None:
                        errors.append(f"{_name(pou)} references missing member {root_name}.{member_name}.")
                        break
                    type_name, bounds = variable_type(var)
                    scope = None
                else:
                    member_map = members(type_name)
                    if member_map is None:
                        # Primitive types may only be followed by bit indices.
                        if member_name.isdigit() and type_name in {"BOOL", "BYTE", "WORD", "DWORD"}:
                            bit = int(member_name); max_bit = {"BOOL": 0, "BYTE": 7, "WORD": 15, "DWORD": 31}[type_name]
                            if bit > max_bit: errors.append(f"{_name(pou)} uses bit {bit} outside {type_name} bounds 0..{max_bit}.")
                            continue
                        errors.append(f"{_name(pou)} references unknown member {member_name} on type {type_name}.")
                        break
                    if member_name not in member_map:
                        errors.append(f"{_name(pou)} references missing member {type_name}.{member_name}.")
                        break
                    var = member_map[member_name]
                    if var is None and type_name == "DUT_Union_Real_Word":
                        type_name, bounds = ("REAL", None) if member_name == "Real_value" else ("WORD", (0, 1))
                    else:
                        type_name, bounds = variable_type(var)
                if index is not None:
                    if bounds is None:
                        if type_name == "DUT_Union_Real_Word" and member_name == "Word_value": bounds = (0, 1)
                        else:
                            errors.append(f"{_name(pou)} indexes non-array member {member_name}[{index}].")
                            break
                    lo, hi = bounds
                    if not lo <= int(index) <= hi:
                        errors.append(f"{_name(pou)} index {index} is outside {member_name} array bounds {lo}..{hi}.")


def _check_modbus_bounds(state: dict, root: etree._Element, errors: list[str]) -> None:
    declared = {}
    gvls = []
    snap_gvl = _snapshot_nodes(state).get("GVL_MODBUS")
    if snap_gvl is not None: gvls.append(snap_gvl)
    gvls.extend(root.xpath("//*[local-name()='globalVars'][@name='GVL_MODBUS']"))
    for gvl in gvls:
        for var in gvl.xpath("./*[local-name()='variable']"):
            name = var.get("name", "")
            dim = var.xpath(".//*[local-name()='dimension'][1]")
            if dim:
                try: declared[name] = (int(dim[0].get("lower")), int(dim[0].get("upper")))
                except (ValueError, TypeError): pass
    for point in state.get("tcp_points", []):
        try:
            from backend.v2_plc import _tcp_address_expression
            mapped = "GVL_MODBUS." + _tcp_address_expression(point, state)
        except Exception:
            mapped = str(point.get("GVL_MODBUS variable", point.get("gvl_modbus_variable", "")))
        match = re.search(r"GVL_MODBUS\.([A-Za-z_]\w*)\[(\d+)\](?:\.(\d+))?", mapped)
        if not match:
            continue
        try:
            name, index, bit = match.group(1), int(match.group(2)), match.group(3)
            bounds = declared.get(name)
            if bounds is None:
                continue
            lower, upper = bounds
            last = index
            if last < lower or last > upper:
                size = max(last - lower + 1, upper + 2 - lower)
                server_size = ((index - lower) * 8 + int(bit) + 1) if bit is not None else size
                errors.append(f"TCP mapping {mapped} exceeds declared array {name}[{lower}..{upper}]; it requires at least {last - lower + 1} array elements and a CODESYS server/device size of at least {server_size} addresses. Update generator bounds and map the device size manually.")
        except (TypeError, ValueError):
            continue


def _check_mapping_changes(state: dict, objects: dict[str, dict], root: etree._Element, warnings: list[str]) -> None:
    current = {_mapped_variable_base(str(v.get("name"))) for v in state.get("mapped_variables", []) if v.get("name")}
    old = {_mapped_variable_base(v) for v in _snapshot_mapped_variables(state)}
    new = {n for n in current - old if "<" not in n and "INSTANCE" not in n.upper()}
    for name in sorted(new):
        warnings.append(f"New mapped variable {name} requires manual mapping in the CODESYS device tree.")
    if any("trigger" in name.casefold() for name in new):
        warnings.append("Communication trigger changes require manual remapping in CODESYS.")


def _check_rtu_mapping_collisions(state: dict, errors: list[str]) -> None:
    by_variable: dict[str, set[str]] = defaultdict(set)
    for row in state.get("rtu_channels", []):
        raw = str(row.get("GVL_MODBUS variable", row.get("gvl_modbus_variable", "")))
        base = _mapped_variable_base(raw)
        instance = str(row.get("instance_id", row.get("instance", "")))
        if raw.startswith("GVL_MODBUS.") and instance:
            by_variable[base].add(instance)
    for name, instances in by_variable.items():
        if len(instances) > 1:
            errors.append(f"RTU mapped variable {name} is shared by multiple instances ({', '.join(sorted(instances))}); assign instance-based GVL_MODBUS names before export.")
