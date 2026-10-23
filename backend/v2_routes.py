"""Authenticated stage-one project files and reviewed imports for Generator v2."""
from __future__ import annotations

import io
import json
import copy
import re
import secrets
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.db import ImportPreview


class ApplyV2Import(BaseModel):
    token: str


class ObjectEdit(BaseModel):
    revision: int
    duts: list[dict] | None = None
    body: str | None = None
    custom_code: str | None = None
    processing_code: str | None = None
    mappings: list[dict] | None = None


class ApplyObjectEdit(BaseModel):
    token: str


def register_v2_routes(app, get_session, current_user, csrf):
    from backend import v2_engine as engine, v2_store as store
    from backend.v2_codesys_routes import register_codesys_routes
    from backend.v2_plc_routes import register_plc_routes

    register_plc_routes(app, get_session, current_user, csrf)
    register_codesys_routes(app, get_session, current_user, csrf)
    from backend.v2_type_creation import register_type_creation_routes
    from backend.v2_instance_editor import register_instance_editor_routes
    register_type_creation_routes(app, get_session, current_user, csrf)
    register_instance_editor_routes(app, get_session, current_user, csrf)
    from backend.v2_exchanges import register_exchange_routes
    register_exchange_routes(app, get_session, current_user, csrf)
    from backend.v2_configuration import register_configuration_routes
    register_configuration_routes(app, get_session, current_user, csrf)
    from backend.v2_code import register_code_routes
    register_code_routes(app, get_session, current_user, csrf)

    def project_for(session):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "The v2 project has not been initialized")
        return state

    def object_view(state, obj, kind):
        typ = obj if kind == "type" else next((t for t in state.get("types", []) if str(t.get("name")) == str(obj.get("type_id")) or str(t.get("id")) == str(obj.get("type_id"))), None)
        if typ is None:
            return None
        instance_name = None if kind == "type" else obj.get("name")
        def related(collection, **fields):
            aliases = {"instance_id": ("instance_id", "instance", "Instance"), "member_name": ("member_name", "member", "Member")}
            return [copy.deepcopy(x) for x in state.get(collection, []) if all(str(next((x.get(a) for a in aliases.get(k, (k,)) if x.get(a) is not None), "")) == str(v) for k, v in fields.items())]
        links = {"tcp_points": related("tcp_points", instance_id=instance_name) if instance_name else [],
                 "rtu_slaves": related("rtu_slaves", instance_id=instance_name) if instance_name else [],
                 "rtu_channels": related("rtu_channels", instance_id=instance_name) if instance_name else [],
                 "io_channels": related("io_channels", instance_id=instance_name) if instance_name else [],
                 "pa_values": related("pa_values", instance_id=instance_name) if instance_name else [],
                 "exchange_calls": [copy.deepcopy(c) for c in state.get("exchange_calls", []) if instance_name and instance_name in {str(c.get("instance_a_id")), str(c.get("instance_b_id"))}]}
        if kind == "type":
            instance_names = {str(i.get("name")) for i in state.get("instances", []) if str(i.get("type_id")) in {str(typ.get("id")), str(typ.get("name"))}}
            for key, collection in (("tcp_points", "tcp_points"), ("rtu_slaves", "rtu_slaves"), ("rtu_channels", "rtu_channels"), ("io_channels", "io_channels"), ("pa_values", "pa_values")):
                links[key] = [copy.deepcopy(x) for x in state.get(collection, []) if str(x.get("instance_id", x.get("instance", x.get("Instance", "")))) in instance_names]
            links["exchange_calls"] = [copy.deepcopy(c) for c in state.get("exchange_calls", []) if str(c.get("instance_a_id")) in instance_names or str(c.get("instance_b_id")) in instance_names]
        duts = copy.deepcopy(typ.get("duts", []))
        for dut in duts:
            lines = [f"TYPE {dut.get('name')} :", "STRUCT"]
            section = None
            for member_index, member in enumerate(dut.get("members", [])):
                if member.get("section") != section and member.get("section"):
                    lines.append(f"    (* {member['section']} *)")
                    section = member.get("section")
                line = f"    {member.get('name')} : {member.get('iec_type')};"
                if member.get("default_value") not in (None, ""):
                    line = line[:-1] + f" := {member['default_value']};"
                if member.get("comment"): line += f" (* {member['comment']} *)"
                lines.append(line)
            lines.extend(["END_STRUCT", "END_TYPE"])
            dut["st"] = "\n".join(lines)
        fb = copy.deepcopy(typ.get("fb", {}))
        # Render the real generated interface, including timers and extra locals.
        from backend.v2_plc import build_program, NS as XML_NS
        from backend.v2_codesys import _st
        generated = build_program(state)
        pou = generated.get(fb.get("name"), {}).get("element")
        declaration = []
        if pou is not None:
            interface = pou.find(XML_NS + "interface")
            if interface is not None:
                labels = {"inputVars": "VAR_INPUT", "outputVars": "VAR_OUTPUT", "inOutVars": "VAR_IN_OUT", "localVars": "VAR", "tempVars": "VAR_TEMP", "externalVars": "VAR_EXTERNAL"}
                for group in interface:
                    local = group.tag.rsplit("}", 1)[-1]
                    if local not in labels:
                        continue
                    rendered = copy.deepcopy(group)
                    rendered.tag = XML_NS + "globalVars"
                    declaration.append(_st(rendered).replace("VAR_GLOBAL", labels[local], 1))
        fb["declaration"] = "\n\n".join(declaration)
        from backend.v2_fb_flow import flow_view, variable_options
        fb.update(flow_view(fb))
        fb["variable_options"] = variable_options(typ, state)
        return {"id": obj.get("id", obj.get("name")), "name": obj.get("name"), "type_id": typ.get("id" if kind == "type" else "name"), "kind": kind,
                "revision": state.get("revision", 0), "duts": duts, "fb": fb, "links": links}

    @app.get("/api/v2/objects/type/{object_id}")
    def get_object_type(object_id: str, session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        obj = next((t for t in state.get("types", []) if str(t.get("id")) == object_id or str(t.get("name")) == object_id), None)
        if obj is None: raise HTTPException(404, "Object type not found")
        return object_view(state, obj, "type")

    @app.get("/api/v2/objects/instance/{object_id}")
    def get_object_instance(object_id: str, session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        obj = next((i for i in state.get("instances", []) if str(i.get("id")) == object_id or str(i.get("name")) == object_id), None)
        if obj is None: raise HTTPException(404, "Instance not found")
        result = object_view(state, obj, "instance")
        if result is None: raise HTTPException(404, "Instance object type not found")
        return result

    @app.post("/api/v2/objects/type/{object_id}/preview")
    def preview_object_edit(object_id: str, body: ObjectEdit, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if body.revision != current.get("revision", 0): raise HTTPException(409, "Project revision changed; reload the object")
        state = copy.deepcopy(current)
        typ = next((t for t in state.get("types", []) if str(t.get("id")) == object_id or str(t.get("name")) == object_id), None)
        if typ is None: raise HTTPException(404, "Object type not found")
        original = next(t for t in current["types"] if t.get("id") == typ.get("id") and t.get("name") == typ.get("name"))
        submitted = body.duts if body.duts is not None else copy.deepcopy(typ.get("duts", []))
        if len(submitted) == len(typ.get("duts", [])):
            submitted = [{**old, **incoming, "role": incoming.get("role", old.get("role")), "name": incoming.get("name", old.get("name"))}
                         for old, incoming in zip(typ.get("duts", []), submitted)]
        errors, warnings, changes = [], [], []
        if [str(d.get("role")) for d in submitted] != [str(d.get("role")) for d in typ.get("duts", [])]: errors.append("DUT roles and order are fixed")
        old_by_role = {str(d.get("role")): d for d in typ.get("duts", [])}
        for dut in submitted:
            role = str(dut.get("role", "")); old = old_by_role.get(role)
            if not old: continue
            if str(dut.get("name")) != str(old.get("name")): errors.append(f"DUT {role} name is fixed")
            seen = set()
            old_members = {str(m.get("name")): m for m in old.get("members", [])}
            for member_index, member in enumerate(dut.get("members", [])):
                name = str(member.get("name", ""))
                if name.upper() in {"INT", "BOOL", "REAL", "WORD", "BYTE", "ARRAY", "STRUCT", "END_STRUCT", "VAR", "END_VAR", "IF", "THEN", "ELSE", "TRUE", "FALSE", "FOR", "WHILE", "FUNCTION", "PROGRAM", "FUNCTION_BLOCK"}: errors.append(f"Reserved IEC keyword {name!r} cannot be a member name")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name): errors.append(f"Invalid IEC member name {name!r} in DUT_{role}")
                if name.casefold() in seen: errors.append(f"Duplicate member {name} in DUT_{role}")
                seen.add(name.casefold())
                prior = old_members.get(name)
                if prior:
                    member["id"] = prior.get("id")
                else:
                    member["id"] = f"{typ.get('id')}:{role}:{name}"
                member["position"] = member_index
                if not re.fullmatch(r"(?:BOOL|BYTE|WORD|DWORD|LWORD|SINT|INT|DINT|LINT|USINT|UINT|UDINT|ULINT|REAL|LREAL|TIME|DATE|TIME_OF_DAY|DATE_AND_TIME|STRING(?:\(\d+\))?|WSTRING(?:\(\d+\))?|ARRAY\s*\[[^]]+\]\s*OF\s*[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)", str(member.get("iec_type", "")), re.I): errors.append(f"Unsupported IEC type {member.get('iec_type')!r} for {role}.{name}")
                iec_type = str(member.get("iec_type", "")).strip()
                array_bounds = re.fullmatch(r"ARRAY\s*\[\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\]\s*OF\s*([A-Za-z_][A-Za-z0-9_]*)", iec_type, re.I)
                if array_bounds and int(array_bounds[1]) > int(array_bounds[2]): errors.append(f"Array lower bound exceeds upper bound for {role}.{name}")
                if re.fullmatch(r"ARRAY\s*\[", iec_type, re.I): errors.append(f"Invalid array type {iec_type!r} for {role}.{name}")
                elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", iec_type) and iec_type.upper() not in {"BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "REAL", "LREAL", "TIME", "DATE", "TIME_OF_DAY", "DATE_AND_TIME", "STRING", "WSTRING"}:
                    known = {str(t.get("name")) for t in state.get("types", [])}
                    known.update(str(d.get("name")) for t in state.get("types", []) for d in t.get("duts", []))
                    known.update(str(o.get("name")) for o in (state.get("snapshot") or {}).get("objects", []))
                    if iec_type not in known: errors.append(f"Unknown derived type {iec_type!r} for {role}.{name}")
                default = member.get("default_value")
                if default not in (None, ""):
                    base = re.sub(r"\s*\[.*", "", iec_type).upper()
                    if base == "BOOL" and str(default).upper() not in {"TRUE", "FALSE", "0", "1"}: errors.append(f"Invalid BOOL default {default!r} for {role}.{name}")
                    limits = {"SINT":(-128,127), "INT":(-32768,32767), "DINT":(-2147483648,2147483647), "LINT":(-9223372036854775808,9223372036854775807), "USINT":(0,255), "BYTE":(0,255), "UINT":(0,65535), "WORD":(0,65535), "UDINT":(0,4294967295), "DWORD":(0,4294967295), "ULINT":(0,18446744073709551615), "LWORD":(0,18446744073709551615)}
                    if base in {"REAL", "LREAL"}:
                        try:
                            from decimal import Decimal, InvalidOperation
                            if not Decimal(str(default)).is_finite(): raise ValueError("non-finite")
                        except (InvalidOperation, ValueError): errors.append(f"Invalid {base} default {default!r} for {role}.{name}")
                    if base in limits:
                        try: value = int(str(default), 0)
                        except ValueError: errors.append(f"Invalid {base} default {default!r} for {role}.{name}")
                        else:
                            if not limits[base][0] <= value <= limits[base][1]: errors.append(f"{base} default {default!r} is out of range for {role}.{name}")
            deleted = {str(m.get("name")) for m in old.get("members", [])} - {str(m.get("name")) for m in dut.get("members", [])}
            for name in sorted(deleted):
                instance_names = {str(i.get("name")) for i in current.get("instances", []) if str(i.get("type_id")) in {str(typ.get("id")), str(typ.get("name"))}}
                ref_collections = ("tcp_points", "rtu_channels", "pa_values", "io_channels")
                mapped = any(str(x.get("member_name", x.get("member", ""))) == name and str(x.get("instance_id", x.get("instance", ""))) in instance_names for collection in ref_collections for x in current.get(collection, []))
                code = str(typ.get("fb", {}).get("body", "")) + "\n" + str(typ.get("fb", {}).get("custom_code", ""))
                if mapped or re.search(rf"\b{re.escape(name)}\b", code): errors.append(f"Cannot delete referenced member {role}.{name}; update its mapping or FB code first")
        if errors: return {"token": None, "errors": errors, "warnings": warnings, "changes": []}
        typ["duts"] = submitted
        if body.body is not None: typ.setdefault("fb", {})["body"] = body.body
        if body.custom_code is not None: typ.setdefault("fb", {})["custom_code"] = body.custom_code
        from backend.v2_fb_flow import ensure_flow, update_flow
        ensure_flow(typ.setdefault("fb", {}))
        flow_errors = update_flow(typ, state, mappings=body.mappings, processing_code=body.processing_code)
        if flow_errors: return {"token": None, "errors": flow_errors, "warnings": [], "changes": []}
        _sync_type_workbook(state, typ)
        # Rebuild and run the same reference consistency checks used for PLCopen
        # exports, while allowing unrelated pre-existing project warnings/errors.
        try:
            from backend.v2_plc import build_program, serialize_program
            from backend.v2_plc_validation import validate_xml
            built = build_program(state)
            checked = validate_xml(serialize_program(built, state, list(built)), state, built)
            old_built = build_program(current)
            old_checked = validate_xml(serialize_program(old_built, current, list(old_built)), current, old_built)
            old_errors = set(old_checked.get("errors", []))
            errors.extend(e for e in checked.get("errors", []) if e not in old_errors)
            warnings.extend(checked.get("warnings", []))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"PLC consistency check failed: {exc}")
        if errors: return {"token": None, "errors": errors, "warnings": warnings, "changes": []}
        changes = []
        for previous, proposed in zip(original.get("duts", []), typ.get("duts", [])):
            old_rows = {str(m.get("name")): m for m in previous.get("members", [])}
            new_rows = {str(m.get("name")): m for m in proposed.get("members", [])}
            for name in new_rows.keys() - old_rows.keys(): changes.append(f"{proposed['name']}: added {name} : {new_rows[name].get('iec_type')}")
            for name in old_rows.keys() - new_rows.keys(): changes.append(f"{proposed['name']}: removed {name}")
            for name in new_rows.keys() & old_rows.keys():
                fields = [field for field in ("iec_type", "default_value", "section", "unit", "comment", "exchange") if new_rows[name].get(field) != old_rows[name].get(field)]
                if fields: changes.append(f"{proposed['name']}.{name}: changed {', '.join(fields)}")
            if [m.get("name") for m in previous.get("members", [])] != [m.get("name") for m in proposed.get("members", [])]: changes.append(f"{proposed['name']}: declaration order updated")
        if any(d.get("role") == "PA" and d != old for d, old in zip(typ.get("duts", []), original.get("duts", []))): warnings.append("Changing DUT_PA can reset persistent parameter values on the next PLC download. Test in a CODESYS copy first.")
        if body.body is not None and body.body != original.get("fb", {}).get("body"): changes.append("Updated FB body")
        if body.custom_code is not None and body.custom_code != original.get("fb", {}).get("custom_code"): changes.append("Updated FB custom code")
        if body.processing_code is not None and body.processing_code != original.get("fb", {}).get("flow", {}).get("processing_code"): changes.append("Updated PROCESSING source")
        if body.mappings is not None: changes.append("Reviewed INPUTS / OUTPUTS mappings")
        token = secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token, owner_id=user.id, state_payload=json.dumps(state, ensure_ascii=False),
            diff_payload=json.dumps({"kind":"v2-object-edit", "base_revision":current.get("revision",0)}, ensure_ascii=False),
            expires_at=datetime.now(timezone.utc)+timedelta(hours=2)))
        session.commit()
        return {"token":token, "errors":errors, "warnings":warnings, "changes":changes}

    @app.post("/api/v2/objects/apply")
    def apply_object_edit(body: ApplyObjectEdit, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token); now = datetime.now(timezone.utc)
        if not preview or preview.owner_id != user.id: raise HTTPException(404, "Object preview not found or expired")
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at.tzinfo is None else preview.expires_at
        if expiry <= now: raise HTTPException(404, "Object preview not found or expired")
        metadata = json.loads(preview.diff_payload)
        if metadata.get("kind") != "v2-object-edit": raise HTTPException(404, "Object preview not found or expired")
        current = project_for(session)
        if current.get("revision", 0) != metadata.get("base_revision"): raise HTTPException(409, "Project changed after preview; create a new preview")
        state = json.loads(preview.state_payload)
        try: saved = store.save_project(session, state, username=user.username, expected_revision=metadata["base_revision"])
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        session.delete(preview); session.commit()
        return {"revision":saved["revision"]}

    def _sync_type_workbook(state, typ):
        filename = typ.get("source_file") or f"TYPE_{typ.get('name')}.xlsx"
        book = state.get("workbooks", {}).get(filename)
        if not book: return
        for dut in typ.get("duts", []):
            sheet = next((s for s in book.get("sheets", []) if s.get("name") == f"DUT_{dut.get('role')}"), None)
            if sheet is None: continue
            headers = [str(h or "") for h in sheet.get("headers", [])]
            rows = []
            for member in dut.get("members", []):
                old_rows = {str(dict(zip(headers, values)).get("Member")): values for values in sheet.get("rows", []) if dict(zip(headers, values)).get("Member") not in (None, "")}
                row = list(old_rows.get(str(member.get("name")), [None] * len(headers)))
                if len(row) < len(headers): row.extend([None] * (len(headers) - len(row)))
                values = {"Section":member.get("section"), "Member":member.get("name"), "Type":member.get("iec_type"), "Default value":member.get("default_value"), "Unit":member.get("unit"), "Comment":member.get("comment"), "Exchange":member.get("exchange")}
                for index, header in enumerate(headers):
                    if header in values: row[index] = values[header]
                rows.append(row)
            sheet["rows"] = rows

    def download(content: bytes, filename: str, media_type: str):
        target = Path(app.state.data_dir) / "exports" / f"v2-{uuid.uuid4().hex}-{filename}"
        target.write_bytes(content)
        return FileResponse(target, filename=filename, media_type=media_type)

    @app.get("/api/v2/project")
    def get_project(session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        types = state.get("types", [])
        workbooks = []
        for filename, workbook in state.get("workbooks", {}).items():
            workbooks.append({"filename": filename, "sheets": [
                {"name": sheet["name"], "state": sheet.get("state", "visible"),
                 "row_count": len(sheet.get("rows", [])), "headers": sheet.get("headers", [])}
                for sheet in workbook.get("sheets", [])]})
        result = {key: value for key, value in state.items()
                  if key not in {"workbooks", "fuxa_csv", "fuxa_template", "source_files"}}
        result.update(phase=8, workbooks=workbooks, validation=engine.validate_project(state), counts={
            "types": len(types), "instances": len(state.get("instances", [])),
            "duts": sum(len(t.get("duts", [])) for t in types),
            "dut_members": sum(len(d.get("members", [])) for t in types for d in t.get("duts", [])),
            "fuxa_signals": len(state.get("fuxa", {}).get("points", [])),
            "tcp_points": len(state.get("tcp_points", [])),
            "exchange_functions": len(state.get("exchange_functions", [])),
        })
        if isinstance(result.get("snapshot"), dict):
            result["snapshot"] = {**result["snapshot"], "object_count": sum(
                obj.get("kind") in {"DUT", "GVL", "POU", "function", "functionBlock", "program"}
                for obj in result["snapshot"].get("objects", []))}
        return result

    @app.get("/api/v2/workbooks/{filename}")
    def get_workbook(filename: str, session=Depends(get_session), user=Depends(current_user)):
        workbook = project_for(session).get("workbooks", {}).get(filename)
        if workbook is None:
            raise HTTPException(404, "Workbook not found")
        return {"filename": filename, **workbook}

    @app.get("/api/v2/exports/fuxa.csv")
    def export_fuxa(session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        errors = engine.validate_project(state).get("errors", [])
        if errors:
            raise HTTPException(409, detail={"errors": errors})
        try:
            content = engine.export_fuxa(state)
        except ValueError as exc:
            raise HTTPException(409, f"FUXA export rejected: {exc}") from exc
        return download(content, "SCADA_V1-devices_v4.csv", "text/csv; charset=utf-8")

    @app.get("/api/v2/exports/{filename}")
    def export_workbook(filename: str, session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        if filename not in state.get("workbooks", {}):
            raise HTTPException(404, "Workbook not found")
        try:
            content = engine.export_workbook(state, filename)
        except ValueError as exc:
            raise HTTPException(409, f"Excel export rejected: {exc}") from exc
        return download(content, filename,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/api/v2/backup")
    def backup(session=Depends(get_session), user=Depends(current_user)):
        payload = {"format": "gaia-generator-v2-backup", "version": 2,
                   "state": project_for(session)}
        return download(json.dumps(payload, ensure_ascii=False, indent=2).encode(),
                        "gaia-generator-v2-backup.json", "application/json")

    @app.post("/api/v2/imports/preview")
    async def preview_import(file: UploadFile = File(...), mode: str = Form("merge"),
                             session=Depends(get_session), user=Depends(current_user),
                             _=Depends(csrf)):
        if mode not in {"merge", "replace"}:
            raise HTTPException(422, "Import mode must be merge or replace")
        filename = Path(file.filename or "").name
        if not filename.lower().endswith(".xlsx"):
            raise HTTPException(415, "Upload a v2 .xlsx workbook")
        data = await file.read(20 * 1024 * 1024 + 1)
        if not data or len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "Workbook must contain between 1 byte and 20 MB")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 1000 or sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError("Workbook expands beyond the supported size")
                if any(x.flag_bits & 1 for x in archive.infolist()):
                    raise ValueError("Encrypted workbooks are not supported")
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, f"Invalid workbook: {exc}") from exc
        current = project_for(session)
        try:
            result = engine.import_workbook(data, filename, current, mode=mode)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"Could not import workbook: {exc}") from exc
        stored = Path(app.state.data_dir) / "imports" / f"v2-{uuid.uuid4().hex}.xlsx"
        stored.write_bytes(data)
        token = secrets.token_urlsafe(32)
        metadata = {"kind": "v2-workbook", "base_revision": current.get("revision", 0),
                    "filename": filename, "mode": mode, "source_file": stored.name,
                    "diff": result.get("diff", {})}
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps(result["state"], ensure_ascii=False),
                                  diff_payload=json.dumps(metadata, ensure_ascii=False),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
        session.commit()
        return {"token": token, "filename": filename, "mode": mode,
                "diff": result.get("diff", {}), "validation": result["validation"]}

    @app.post("/api/v2/imports/apply")
    def apply_import(body: ApplyV2Import, session=Depends(get_session),
                     user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        if not preview or preview.owner_id != user.id:
            raise HTTPException(404, "Import preview not found or expired")
        expiry = preview.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            raise HTTPException(404, "Import preview not found or expired")
        metadata = json.loads(preview.diff_payload)
        if metadata.get("kind") != "v2-workbook":
            raise HTTPException(404, "Import preview not found or expired")
        state = json.loads(preview.state_payload)
        current = project_for(session)
        if current.get("revision", 0) != metadata.get("base_revision"):
            raise HTTPException(409, "Project changed after preview; create a new preview")
        validation = engine.validate_project(state)
        if validation.get("errors"):
            raise HTTPException(422, detail=validation)
        try:
            saved = store.save_project(session, state, username=user.username,
                                       expected_revision=metadata["base_revision"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.delete(preview)
        session.commit()
        return {"revision": saved["revision"], "validation": validation}
