"""Safe preview and merge engine for PLCopen snapshots exported by CODESYS."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import uuid

from lxml import etree

PLC = "http://www.plcopen.org/xml/tc6_0200"
NS = {"p": PLC}
OID = "http://www.3s-software.com/plcopenxml/objectid"
STRUCT = "http://www.3s-software.com/plcopenxml/projectstructure"
GVL_DATA = "http://www.3s-software.com/plcopenxml/globalvars"
POU_DATA = "http://www.3s-software.com/plcopenxml/pou"
MAX_BYTES = 20 * 1024 * 1024
_TAGS = {"dataType": "DUT", "globalVars": "GVL", "pou": "POU"}


def _local(e):
    return etree.QName(e).localname if isinstance(e.tag, str) else ""


def _name(e):
    return e.get("name") or e.get("Name") or ""


def _metadata_kind(e):
    return e.get("pouType", "POU") if _local(e) == "pou" else _TAGS.get(_local(e), "POU")


def _declaration(e):
    interface = e.find("p:interface", NS)
    return "".join(interface.itertext()) if interface is not None else ""


def _fingerprint(e):
    from .v2_plc_validation import _fingerprint as semantic_fingerprint
    return semantic_fingerprint(e)


def _objects(root):
    result = {}
    types = root.find("p:types", NS)
    if types is not None:
        for group_name in ("dataTypes", "pous"):
            group = types.find("p:" + group_name, NS)
            if group is not None:
                for el in group:
                    if _local(el) in _TAGS and _name(el):
                        result[_name(el)] = el
    for el in root.iter():
        if _local(el) in _TAGS and _name(el):
            # only objects in PLCopen types, or direct resource-level GVLs
            parent = el.getparent()
            if (parent is not None and _local(parent) in {"dataTypes", "pous", "resource"}) or (
                    parent is not None and _local(parent) == "data" and
                    parent.get("name") in {GVL_DATA, POU_DATA}):
                result[_name(el)] = el
    return result


def _duplicate_object_names(root):
    found, duplicates = set(), []
    for el in root.iter():
        if _local(el) not in _TAGS or not _name(el):
            continue
        parent = el.getparent()
        is_object = ((parent is not None and _local(parent) in {"dataTypes", "pous", "resource"}) or
                     (parent is not None and _local(parent) == "data" and parent.get("name") in {GVL_DATA, POU_DATA}))
        if is_object and _name(el) in found:
            duplicates.append(_name(el))
        elif is_object:
            found.add(_name(el))
    return duplicates


def _safe_parse(data: bytes):
    if not isinstance(data, bytes):
        raise ValueError("Snapshot must be provided as bytes.")
    if len(data) > MAX_BYTES:
        raise ValueError("Snapshot exceeds the 20 MiB size limit.")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False, huge_tree=False, recover=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("DOCTYPE declarations are not accepted.")
    if _local(root) != "project" or etree.QName(root).namespace != PLC:
        raise ValueError("Expected a PLCopen project XML document.")
    return root


def _ids(root, names):
    result = {}
    duplicates = []
    for wrapper in root.iter():
        if _local(wrapper) != "Object":
            continue
        name, oid = wrapper.get("Name"), wrapper.get("ObjectId")
        if name in names and oid:
            if name in result and result[name] != oid:
                duplicates.append(f"Multiple ObjectIds for {name}.")
            result[name] = oid
    for name, el in names.items():
        for d in el.xpath(".//p:data[@name=$oid]/p:ObjectId", namespaces=NS, oid=OID):
            value = (d.text or "").strip()
            if value:
                if name in result and result[name] != value:
                    duplicates.append(f"Conflicting ObjectIds for {name}.")
                result[name] = value
    seen = {}
    for name, value in result.items():
        if value in seen and seen[value] != name:
            duplicates.append(f"ObjectId {value} is shared by {seen[value]} and {name}.")
        seen[value] = name
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            duplicates.append(f"Invalid ObjectId for {name}: {value!r}.")
    return result, duplicates


def _folders(root, names):
    result = {}
    ps = next((x for x in root.iter() if _local(x) == "ProjectStructure"), None)
    def walk(node, prefix=()):
        for child in node:
            if _local(child) == "Folder":
                walk(child, prefix + ((child.get("Name") or ""),))
            elif _local(child) == "Object":
                if child.get("Name") in names:
                    result[child.get("Name")] = "/".join(x for x in prefix if x)
                walk(child, prefix)
    if ps is not None:
        walk(ps)
    return result


def _headers(root):
    def attrs(tag):
        el = root.find("p:" + tag, NS)
        return dict(el.attrib) if el is not None else {}
    return attrs("fileHeader"), attrs("contentHeader")


def _st(e):
    if _local(e) in {'dataType', 'globalVars'}:
        def type_text(node):
            if node is None:
                return '?'
            if _local(node) == 'derived':
                return node.get('name', '?')
            if _local(node) == 'array':
                dims = ', '.join(f"{d.get('lower')}..{d.get('upper')}" for d in node.findall('p:dimension', NS))
                return f"ARRAY[{dims}] OF {type_text(node.find('p:baseType/*', NS))}"
            return _local(node).upper()
        def initial(node):
            if node is None:
                return ''
            simple = node.find('p:simpleValue', NS)
            if simple is not None:
                return simple.get('value', '')
            values = node.findall('p:structValue/p:value', NS)
            return '(' + ', '.join(f"{v.get('member')} := {initial(v)}" for v in values) + ')' if values else ''
        variables = e.findall('p:baseType/p:struct/p:variable', NS) if _local(e) == 'dataType' else e.findall('p:variable', NS)
        lines = []
        for var in variables:
            value = initial(var.find('p:initialValue', NS))
            lines.append(f"\t{var.get('name')} : {type_text(var.find('p:type/*', NS))}" + (f' := {value}' if value else '') + ';')
        if _local(e) == 'dataType':
            return f"TYPE {_name(e)} :\nSTRUCT\n" + '\n'.join(lines) + '\nEND_STRUCT\nEND_TYPE'
        qualifiers = (' PERSISTENT' if e.get('persistent') == 'true' else '') + (' RETAIN' if e.get('retain') == 'true' else '')
        return f'VAR_GLOBAL{qualifiers}\n' + '\n'.join(lines) + '\nEND_VAR'
    found = e.xpath(".//*[local-name()='ST']/*[local-name()='xhtml']")
    return "".join((x.text or "") for x in found)


def _current_objects(current):
    from .v2_plc import build_program
    generated = build_program(current)
    return generated if isinstance(generated, dict) else {}


def preview_codesys(data: bytes, filename: str, current: dict, scope: str = "partial") -> dict:
    """Return a proposed snapshot and reviewable per-object comparison; never mutates current."""
    errors, warnings, changes = [], [], []
    if scope not in {"partial", "full"}:
        return {"state": copy.deepcopy(current), "changes": [], "errors": ["Scope must be 'partial' or 'full'."], "warnings": []}
    try:
        incoming_root = _safe_parse(data)
        duplicates = _duplicate_object_names(incoming_root)
        if duplicates:
            raise ValueError("Duplicate PLC object names: " + ", ".join(sorted(set(duplicates))) + ".")
        incoming = _objects(incoming_root)
        if not incoming:
            raise ValueError("No PLCopen DUT, GVL, or POU objects were found.")
        incoming_ids, id_errors = _ids(incoming_root, incoming)
        if id_errors:
            raise ValueError(" ".join(id_errors))
    except (ValueError, etree.XMLSyntaxError) as exc:
        return {"state": copy.deepcopy(current), "changes": [], "errors": [str(exc)], "warnings": []}
    if scope == "full":
        warnings.append("Full snapshot replaces the known object baseline; absent objects may have been omitted. Engineering data is retained.")
    old = (current.get("snapshot") or {})
    old_root = None
    old_objects = {}
    if old.get("xml"):
        try:
            old_root = _safe_parse(old["xml"].encode() if isinstance(old["xml"], str) else old["xml"])
            old_objects = _objects(old_root)
        except (ValueError, etree.XMLSyntaxError, TypeError):
            warnings.append("Previous snapshot XML could not be parsed; incoming objects are treated as new.")
    old_ids = dict(old.get("object_ids") or {})
    old_folders = {o.get("name"): o.get("folder", "") for o in old.get("objects", []) if isinstance(o, dict)}
    old_file, old_content = _headers(old_root) if old_root is not None else (old.get("file_header", {}), old.get("content_header", {}))
    incoming_file, incoming_content = _headers(incoming_root)
    file_header, content_header = dict(old_file), dict(old_content)
    file_header.update(incoming_file)
    content_header.update(incoming_content)
    def header_node(tag):
        node = incoming_root.find("p:" + tag, NS)
        if node is None and old_root is not None:
            node = old_root.find("p:" + tag, NS)
        result = copy.deepcopy(node) if node is not None else etree.Element("{" + PLC + "}" + tag)
        result.attrib.update(file_header if tag == "fileHeader" else content_header)
        return result
    try:
        generated = _current_objects(current)
    except Exception:
        generated = {}
    # Accept generator shapes returned by v2_plc.generate: {name: {element,...}}.
    gen_elements = {n: v.get("element") for n, v in generated.items() if isinstance(v, dict) and isinstance(v.get("element"), etree._Element)}
    folders = _folders(incoming_root, incoming)
    for name, el in incoming.items():
        before = old_objects.get(name)
        model = gen_elements.get(name)
        inc_fp = _fingerprint(el)
        before_fp = _fingerprint(before) if before is not None else None
        model_fp = _fingerprint(model) if model is not None else None
        if before is None:
            status = "new"
        elif inc_fp == before_fp and (model_fp is None or model_fp == before_fp):
            status = "unchanged"
        elif inc_fp != before_fp and (model_fp is None or model_fp == before_fp):
            status = "codesys_only"
        elif inc_fp == before_fp and model_fp != before_fp:
            status = "generator_only"
        elif inc_fp == model_fp:
            status = "unchanged"
        else:
            status = "conflict"
        oid_changed = name in old_ids and name in incoming_ids and old_ids[name] != incoming_ids[name]
        folder_changed = name in old_folders and name in folders and old_folders[name] != folders[name]
        incoming_vs_model = model_fp is not None and inc_fp != model_fp
        model_obj = generated.get(name, {})
        kind = model_obj.get("kind") or _TAGS.get(_local(el), "POU")
        incoming_interface = el.find("p:interface", NS)
        model_interface = model.find("p:interface", NS) if model is not None else None
        interface_same = (incoming_interface is not None and model_interface is not None
                          and _fingerprint(incoming_interface) == _fingerprint(model_interface))
        can_adopt = kind == "POU" and name.startswith(("FB_", "FNC_")) and interface_same
        incoming_changed = before_fp is None or inc_fp != before_fp
        conflict = incoming_vs_model and incoming_changed
        changes.append({"name": name, "kind": kind, "status": status,
                        "identity_changed": oid_changed, "folder_changed": folder_changed,
                        "codesys_changed": before_fp is not None and inc_fp != before_fp,
                        "generator_changed": before_fp is not None and model_fp is not None and model_fp != before_fp,
                        "conflict": conflict, "can_adopt": can_adopt if conflict else False,
                        "before_st": _st(before) if before is not None else "",
                        "imported_st": _st(el), "generated_st": _st(model) if model is not None else ""})
    if scope == "full":
        for name, el in old_objects.items():
            if name not in incoming:
                changes.append({"name": name, "kind": _metadata_kind(el), "status": "absent",
                                "identity_changed": False, "folder_changed": False, "codesys_changed": False,
                                "generator_changed": False, "conflict": False, "can_adopt": False,
                                "before_st": _st(el), "imported_st": "", "generated_st": ""})
    # Merge only incoming PLCopen objects into the snapshot baseline. Preserve headers and wrappers from this upload,
    # but never copy Application resource configuration beyond actual GVL objects.
    merged = {} if scope == "full" else dict(old_objects)
    merged.update(incoming)
    all_ids = dict(old_ids) if scope == "partial" else {}
    all_ids.update(incoming_ids)
    id_owners = {}
    for object_name, object_id in all_ids.items():
        if object_id in id_owners and id_owners[object_id] != object_name:
            errors.append(f"ObjectId {object_id} is shared by {id_owners[object_id]} and {object_name} in the merged snapshot.")
        id_owners[object_id] = object_name
    if errors:
        return {"state": copy.deepcopy(current), "changes": changes, "errors": errors, "warnings": warnings}
    all_folders = dict(old_folders) if scope == "partial" else {}
    all_folders.update(folders)
    conflicts = copy.deepcopy(current.get("codesys_conflicts") or {})
    for row in changes:
        name = row["name"]
        if row["conflict"]:
            conflicts[name] = {"snapshot_fingerprint": _fingerprint(incoming[name]),
                               "model_fingerprint": _fingerprint(gen_elements[name]) if name in gen_elements else None}
        elif name in conflicts and row["status"] in {"unchanged", "codesys_only", "generator_only"}:
            # Keep unresolved conflict evidence until an explicit resolution workflow clears it.
            pass
    # Construct a canonical free-object PLCopen document; wrapper resource/device data is excluded.
    out = etree.Element("{" + PLC + "}project", nsmap={None: PLC})
    out.append(header_node("fileHeader"))
    out.append(header_node("contentHeader"))
    types = etree.SubElement(out, "{" + PLC + "}types")
    dts = etree.SubElement(types, "{" + PLC + "}dataTypes")
    pous = etree.SubElement(types, "{" + PLC + "}pous")
    instances = etree.SubElement(out, "{" + PLC + "}instances")
    etree.SubElement(instances, "{" + PLC + "}configurations")
    add_data = etree.SubElement(out, "{" + PLC + "}addData")
    for name, el in sorted(merged.items()):
        clone = copy.deepcopy(el)
        if _local(clone) == "dataType": dts.append(clone)
        elif _local(clone) == "pou": pous.append(clone)
        elif _local(clone) == "globalVars":
            gvl_data = etree.SubElement(add_data, "{" + PLC + "}data", name=GVL_DATA, handleUnknown="implementation")
            gvl_data.append(clone)
    structure_data = etree.SubElement(add_data, "{" + PLC + "}data", name=STRUCT, handleUnknown="discard")
    structure = etree.SubElement(structure_data, "{" + PLC + "}ProjectStructure")
    for name in sorted(merged):
        cursor = structure
        for folder in (part for part in all_folders.get(name, "").split("/") if part):
            child = next((x for x in cursor if _local(x) == "Folder" and x.get("Name") == folder), None)
            if child is None:
                child = etree.SubElement(cursor, "{" + PLC + "}Folder", Name=folder)
            cursor = child
        attrs = {"Name": name}
        if all_ids.get(name):
            attrs["ObjectId"] = all_ids[name]
        etree.SubElement(cursor, "{" + PLC + "}Object", **attrs)
    xml = etree.tostring(out, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8")
    snapshot_objects = [{"name": n, "id": all_ids.get(n), "folder": all_folders.get(n, ""),
                         "kind": _metadata_kind(e), "declaration": _declaration(e), "body": _st(e)}
                        for n, e in merged.items()]
    proposed = copy.deepcopy(current)
    proposed["codesys_conflicts"] = conflicts
    proposed["snapshot"] = {"filename": filename, "xml": xml, "object_count": len(merged), "object_ids": all_ids,
                            "objects": snapshot_objects, "file_header": file_header,
                            "content_header": content_header,
                            "imported_at": datetime.now(timezone.utc).isoformat()}
    return {"state": proposed, "changes": changes, "errors": errors, "warnings": warnings}
