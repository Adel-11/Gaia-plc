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


class ApplyTypeCreate(BaseModel):
    token: str


def _identifier(value):
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value or ""))) and str(value).upper() not in IEC_RESERVED


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
        if request.supervision is not None:
            typ["supervision"] = copy.deepcopy(request.supervision)
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
        if request.standard_modes_from:
            modes_type = next((t for t in current.get("types", []) if str(t.get("name")) == request.standard_modes_from or str(t.get("id")) == request.standard_modes_from), None)
            if modes_type is None:
                errors.append("Source type for standard modes was not found.")
            elif "UPI" not in str(modes_type.get("kind", "")).upper():
                errors.append("Standard modes must be copied from a Functional unit (UPI) type.")
            else:
                for role in ("COM", "EXC"):
                    destination = next((d for d in typ["duts"] if d.get("role") == role), None)
                    source_dut = next((d for d in modes_type.get("duts", []) if d.get("role") == role), None)
                    if destination and source_dut:
                        def is_mode_member(member):
                            name = str(member.get("name", "")).upper()
                            return any(part in name for part in ("LOCAL", "AUTO"))
                        selected = [copy.deepcopy(m) for m in source_dut.get("members", []) if is_mode_member(m)]
                        have = {str(m.get("name", "")).casefold() for m in destination.get("members", [])}
                        for member in selected:
                            if str(member.get("name", "")).casefold() not in have:
                                member["position"] = len(destination["members"])
                                destination["members"].append(member)
                # UPI mode FBs contain the complete local/auto behavior and
                # only use the shared COM, EXC, and INT roles.
                typ["fb"]["body"] = _replace_st_identifiers(modes_type.get("fb", {}).get("body", ""), {
                    str(modes_type.get("name")): request.name,
                    str(modes_type.get("fb", {}).get("name", "")): typ["fb"]["name"],
                })
        if request.body is not None: typ["fb"]["body"] = request.body
        if request.custom_code is not None: typ["fb"]["custom_code"] = request.custom_code
        if request.rtu_default is not None: typ["rtu_default"] = copy.deepcopy(request.rtu_default)
        if request.rtu is not None: typ["rtu"] = copy.deepcopy(request.rtu)
        # PA import rows always follow the finalized DUT_PA members. Keep any
        # richer source comments by member name while refreshing type/default.
        previous_pa = {r.get("Member"): r for r in (typ.get("templates") or {}).get("TEMPLATE_PA", [])}
        typ.setdefault("templates", {})["TEMPLATE_PA"] = _pa_templates(typ)
        for row in typ["templates"]["TEMPLATE_PA"]:
            old_row = previous_pa.get(row.get("Member"), {})
            for key in ("Comment", "Changed from supervision"):
                if old_row.get(key) not in (None, ""): row[key] = old_row[key]
        if request.rtu is not None:
            typ.setdefault("templates", {})["TEMPLATE_RTU"] = _rtu_templates(typ)
        if request.supervision is not None:
            typ["supervision"] = copy.deepcopy(request.supervision)
            tcp_rows, tcp_errors = _tcp_templates(typ, request.supervision)
            typ.setdefault("templates", {})["TEMPLATE_TCP"] = tcp_rows
            errors.extend(tcp_errors)
        if not any(d.get("role") == "INT" for d in typ["duts"]):
            typ["duts"].append({"id": f"{request.name}:DUT_INT", "name": f"DUT_INT_{request.name}", "role": "INT", "members": [{"id": f"{request.name}:INT:0", "position": 0, "name": "SPARE", "iec_type": "BOOL"}]})
        for d in typ["duts"]:
            for m in d.get("members", []):
                if not _identifier(m.get("name")): errors.append(f"Invalid member name in {d['name']}: {m.get('name')}")
        state = copy.deepcopy(current)
        state["types"].append(typ)
        state.setdefault("workbooks", {})[typ["source_file"]] = _book_for_type(engine, typ, typ["source_file"])
        # Re-normalize from the workbook so type Excel re-import is lossless.
        normalized = engine._normalize_type(state["workbooks"][typ["source_file"]])
        normalized["fb"].update(body=typ["fb"].get("body", ""), custom_code=typ["fb"].get("custom_code", ""))
        if typ["fb"].get("interface_xml"):
            normalized["fb"]["interface_xml"] = typ["fb"]["interface_xml"]
        normalized["supervision"] = copy.deepcopy(typ.get("supervision", []))
        normalized["templates"] = copy.deepcopy(typ.get("templates", normalized.get("templates", {})))
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
        return state, errors, warnings

    @app.post("/api/v2/types/preview")
    def preview_type(request: TypeCreate, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if request.revision != current.get("revision", 0): raise HTTPException(409, "Project revision changed; reload before creating a type")
        state, errors, warnings = construct(current, request)
        program_objects = {}
        if state and not errors:
            try:
                program_objects = plc.build_program(state)
                xml = plc.serialize_program(program_objects, state, list(program_objects))
                from backend.v2_plc_validation import validate_xml
                checked = validate_xml(xml, state, program_objects)
                errors.extend(checked.get("errors", [])); warnings.extend(checked.get("warnings", []))
            except Exception as exc: errors.append(f"PLCopen validation failed: {exc}")
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
        saved = store.save_project(session, state, username=getattr(user, "username", "admin"), expected_revision=current.get("revision", 0))
        session.delete(preview); session.commit()
        return {"revision": saved.get("revision"), "type": next(t for t in saved["types"] if t.get("source_file") and t["source_file"] not in current.get("workbooks", {}))}
