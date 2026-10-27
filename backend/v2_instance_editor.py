"""Reviewed instance creation and configuration edits for Generator v2."""
from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from backend.db import ImportPreview
from backend.v2_engine import _sheet_map, _member_from_var


class Preview(BaseModel):
    revision: int
    action: str
    instance_id: str | None = None
    name: str | None = None
    type_id: str | None = None
    values: dict = {}
    io_channels: list[dict] = []
    tcp_points: list[dict] = []
    rtu_slaves: list[dict] = []
    rtu_channels: list[dict] = []


class Apply(BaseModel):
    token: str


def _name(row):
    return str(row.get("name", row.get("Instance", row.get("instance_id", ""))))


def _member_rows(typ, template_key):
    return copy.deepcopy((typ.get("templates") or {}).get(template_key, []))


def _replace_instance(row, instance):
    out = copy.deepcopy(row)
    for key, value in list(out.items()):
        if isinstance(value, str):
            out[key] = value.replace("<INSTANCE>", instance)
    if "Instance" in out: out["Instance"] = instance
    return out


def _append_sheet_row(book, sheet_name, headers, row):
    sheet = _sheet_map(book).get(sheet_name)
    if sheet is None:
        sheet = {"name": sheet_name, "headers": list(headers), "rows": []}
        book.setdefault("sheets", []).append(sheet)
    columns = [str(h or "") for h in sheet.get("headers", [])]
    sheet.setdefault("rows", []).append([row.get(h) for h in columns])


def _rewrite_rows(book, sheet_names, rows, key_field):
    """Write normalized workbook-backed rows while retaining sheet structure."""
    for sheet_name in sheet_names:
        sheet = _sheet_map(book).get(sheet_name)
        if sheet is None: continue
        headers = [str(h or "") for h in sheet.get("headers", [])]
        subset = [r for r in rows if str(r.get("_sheet", r.get("sheet", ""))) == sheet_name]
        sheet["rows"] = [[r.get(h) for h in headers] for r in subset]


def _set_or_append(book, sheet_name, row):
    sheet = _sheet_map(book).get(sheet_name)
    if sheet is None:
        return
    headers = [str(h or "") for h in sheet.get("headers", [])]
    sheet.setdefault("rows", []).append([row.get(h) for h in headers])


def _sync_workbooks(state):
    """Mirror persistent normalized edits into their source workbook rows."""
    workbooks = state.get("workbooks", {})
    tcp = workbooks.get("MODBUS_TCP.xlsx")
    if tcp:
        for sheet_name in ("COILS", "DISCRETE_INPUTS", "INPUT_REGISTERS", "HOLDING_REGISTERS"):
            sheet = _sheet_map(tcp).get(sheet_name)
            if sheet:
                headers = [str(h or "") for h in sheet.get("headers", [])]
                sheet["rows"] = [[r.get(h) for h in headers] for r in state.get("tcp_points", []) if str(r.get("sheet", "")).upper() == sheet_name]
    rtu = workbooks.get("MODBUS_RTU.xlsx")
    if rtu:
        sh = _sheet_map(rtu).get("SLAVES")
        if sh:
            headers = [str(h or "") for h in sh.get("headers", [])]
            sh["rows"] = [[r.get(h) for h in headers] for r in state.get("rtu_slaves", [])]
        for sh in list(rtu.get("sheets", [])):
            name = sh.get("name")
            if name not in {"README", "LINES", "SLAVES", "LISTS"}:
                headers = [str(h or "") for h in sh.get("headers", [])]
                sh["rows"] = [[r.get(h) for h in headers] for r in state.get("rtu_channels", []) if str(r.get("instance", r.get("instance_id", ""))) == str(name)]
        alive = {str(r.get("instance", r.get("instance_id", ""))) for r in state.get("rtu_channels", [])}
        rtu["sheets"] = [sh for sh in rtu.get("sheets", []) if sh.get("name") in {"README", "LINES", "SLAVES", "LISTS"} or sh.get("name") in alive]
    pa = workbooks.get("PARAMETERS_PA.xlsx")
    if pa:
        existing_pa_sheets = _sheet_map(pa)
        for row in state.get("pa_values", []):
            instance_name = str(row.get("instance", row.get("instance_id", row.get("Instance", ""))))
            if instance_name and instance_name not in existing_pa_sheets:
                template = next((sh for sh in pa.get("sheets", []) if sh.get("name") not in {"README", "LISTS"}), None)
                if template:
                    pa.setdefault("sheets", []).append({"name": instance_name, "headers": copy.deepcopy(template.get("headers", [])), "rows": []})
                    existing_pa_sheets[instance_name] = pa["sheets"][-1]
        for sh in list(pa.get("sheets", [])):
            name = sh.get("name")
            if name not in {"README", "LISTS"}:
                headers = [str(h or "") for h in sh.get("headers", [])]
                sh["rows"] = [[r.get(h) for h in headers] for r in state.get("pa_values", []) if str(r.get("instance", r.get("instance_id", ""))) == str(name)]
        alive = {str(r.get("instance", r.get("instance_id", ""))) for r in state.get("pa_values", [])}
        pa["sheets"] = [sh for sh in pa.get("sheets", []) if sh.get("name") in {"README", "LISTS"} or sh.get("name") in alive]
    io_book = workbooks.get("IO_PHYSICAL.xlsx")
    if io_book:
        sh = _sheet_map(io_book).get("IO")
        if sh:
            headers = [str(h or "") for h in sh.get("headers", [])]
            sh["rows"] = [[r.get(h) for h in headers] for r in state.get("io_channels", [])]
    exc = workbooks.get("EXCHANGES_EXC.xlsx")
    if exc:
        sh = _sheet_map(exc).get("CALLS")
        if sh:
            headers = [str(h or "") for h in sh.get("headers", [])]
            sh["rows"] = [[r.get(h) for h in headers] for r in state.get("exchange_calls", [])]


def _free_addresses(state, client, table, count):
    from backend.v2_addressing import free_addresses
    return free_addresses(state, client, table, count)


def _tcp_table(row):
    comment=str(row.get("Comment", ""))
    match=re.search(r"Paste in\s+(COILS|DISCRETE_INPUTS|INPUT_REGISTERS|HOLDING_REGISTERS)",comment,re.I)
    if match: return match.group(1).upper()
    variable=str(row.get("GVL_MODBUS variable", ""))
    for table in ("COILS","DISCRETE_INPUTS","INPUT_REGISTERS","HOLDING_REGISTERS"):
        if table.casefold() in variable.casefold(): return table
    return "HOLDING_REGISTERS"


def _tcp_gvl(table):
    return {"COILS":"Coils_TCP_HMI","DISCRETE_INPUTS":"Discret_input_TCP_HMI",
            "INPUT_REGISTERS":"Input_register_TCP_HMI","HOLDING_REGISTERS":"Holding_register_TCP_HMI"}.get(table,"Holding_register_TCP_HMI")


def _tcp_variable(table,address):
    table=str(table).casefold(); n=int(address); base=_tcp_gvl(table.upper())
    if table in {"coils","discrete_inputs"}:
        zero=n-(10001 if table=="discrete_inputs" else 1)
        if zero<0: raise ValueError(f"Address {n} is outside the {table} range")
        return f"GVL_MODBUS.{base}[{zero//8}].{zero%8}"
    return f"GVL_MODBUS.{base}[{n-1}]"


def _tcp_range(client,table,state=None):
    if state is not None:
        from backend.v2_addressing import client_ranges
        return client_ranges(state,str(client))[str(table).casefold()]
    lo={"coils":1,"discrete_inputs":10001,"input_registers":1,"holding_registers":1}.get(str(table).casefold(),1)
    return lo+(1000 if str(client).upper()=="HMI" else 0),lo+999+(1000 if str(client).upper()=="HMI" else 0)


def _type(state, type_id):
    return next((t for t in state.get("types", []) if str(t.get("id")) == str(type_id)
                 or str(t.get("name")) == str(type_id)), None)


def _connect_bound_upis(state):
    """Materialize local bound functions, then create each exchange call once."""
    types = {str(t.get("id", t.get("name"))): t for t in state.get("types", [])}
    types.update({str(t.get("name")): t for t in state.get("types", [])})
    instances = state.get("instances", [])
    existing = {(str(c.get("function_id") or c.get("FC")), str(c.get("instance_a_id") or c.get("Instance A")),
                 str(c.get("instance_b_id") or c.get("Instance B"))) for c in state.get("exchange_calls", [])}
    calls = []
    for equipment in instances:
        equipment_type = types.get(str(equipment.get("type_id")))
        if not equipment_type or "UPI" in str(equipment_type.get("kind", "")).upper():
            continue
        equipment_name = _name(equipment)
        type_bindings = equipment_type.get("upi_bindings", [])
        if type_bindings:
            # Type definitions are shared across projects, while exchange
            # functions and their workbook rows belong to the active project.
            # Rebuild/upsert each function locally from the canonical binding
            # mapping before creating calls that reference it.
            from backend.v2_type_creation import _prepare_upi_bindings
            errors, materialized = _prepare_upi_bindings(state, equipment_type, type_bindings)
            if errors:
                state.setdefault("validation_errors", []).extend(errors)
                continue
            if materialized:
                equipment_type["upi_bindings"] = materialized
                type_bindings = materialized
        for binding in type_bindings:
            upi_type_id = str(binding.get("upi_type_id", ""))
            function_id = str(binding.get("exchange_function_id") or f"FC_EXC_{equipment_type.get('name')}_{types.get(upi_type_id, {}).get('name', upi_type_id)}")
            upi_type = types.get(upi_type_id)
            if not upi_type:
                continue
            for upi in instances:
                if str(upi.get("type_id")) not in {str(upi_type.get("id", upi_type.get("name"))), str(upi_type.get("name"))}:
                    continue
                upi_name = _name(upi)
                key = (function_id, equipment_name, upi_name)
                if key in existing:
                    continue
                call = {"id": f"{function_id}:{equipment_name}:{upi_name}", "function_id": function_id,
                        "instance_a_id": equipment_name, "instance_b_id": upi_name, "FC": function_id,
                        "Instance A": equipment_name, "Instance B": upi_name,
                        "parameter_a_value": f"GVL_EXC.EXC_{equipment_name}",
                        "parameter_b_value": f"GVL_EXC.EXC_{upi_name}"}
                calls.append(call); existing.add(key)
    if calls:
        state.setdefault("exchange_calls", []).extend(calls)
        try:
            from backend.v2_exchanges import _sync as sync_exchanges
            sync_exchanges(state)
        except (KeyError, ValueError, TypeError):
            # The call rows remain in the normalized model; project validation
            # will report malformed exchange metadata during the preview.
            pass


def _clear_virtual_io_for_instance(state, name):
    """Keep virtual channels but clear links when their target instance is removed."""
    for row in state.get("virtual_io", []):
        if str(row.get("instance_id", row.get("Instance", ""))) == str(name):
            for field in ("instance_id", "Instance", "member_name", "GVL_IO member"):
                row.pop(field, None)


def _pa_defaults(state, instance):
    name = _name(instance)
    typ = _type(state, instance.get("type_id"))
    dut = next((d for d in (typ or {}).get("duts", []) if d.get("role") == "PA"), None)
    rows = []
    for index, member in enumerate((dut or {}).get("members", [])):
        member_name = str(member.get("name") or "")
        if not member_name:
            continue
        value = member.get("default_value")
        rows.append({
            "id": f"{name}:{member_name}", "Instance": name, "instance": name, "instance_id": name,
            "Member": member_name, "member_name": member_name, "Type": member.get("iec_type"),
            "data_type": member.get("iec_type"), "Default value": value, "default_value": value,
            "Instance value": value, "value": value, "Section": member.get("section"),
            "Unit": member.get("unit"), "Comment": member.get("comment"),
            "Changed from supervision": False, "position": member.get("position", index),
        })
    return rows


def _pa_editor_rows(state, instance):
    name = _name(instance)
    saved = {str(row.get("Member", row.get("member_name", ""))): row
             for row in state.get("pa_values", [])
             if str(row.get("instance_id", row.get("instance", row.get("Instance", "")))) == name}
    rows = []
    for default in _pa_defaults(state, instance):
        member = str(default["Member"])
        rows.append({**default, **copy.deepcopy(saved.get(member, {}))})
    return rows


def _remove_program_membership(state, name):
    membership = state.get("program_membership")
    if not isinstance(membership, dict):
        return
    for members in membership.values():
        if isinstance(members, list):
            members[:] = [member for member in members if str(member) != str(name)]


def _add_instance(state, name, typ, values=None, warnings=None):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise ValueError("Instance name must start with a letter and contain only letters, numbers, and underscores")
    if any(_name(i).casefold() == name.casefold() for i in state.get("instances", [])):
        raise ValueError(f"Instance {name} already exists")
    if not typ:
        raise ValueError("Choose an object type")
    from backend.v2_program_membership import freeze_membership
    freeze_membership(state)
    state.setdefault("instances", []).append({"id": name, "name": name, "type_id": typ.get("id", typ.get("name"))})
    return []


def _configuration(state, instance):
    name = _name(instance)
    by_instance = lambda key: [copy.deepcopy(r) for r in state.get(key, []) if str(r.get("instance_id", r.get("instance", r.get("Instance", "")))) == name]
    pa_rows = _pa_editor_rows(state, instance)
    return {"revision": state.get("revision", 0), "instance": copy.deepcopy(instance),
            "pa_values": pa_rows, "io_channels": by_instance("io_channels"),
            "tcp_points": by_instance("tcp_points"), "rtu_slaves": [copy.deepcopy(r) for r in state.get("rtu_slaves", []) if str(r.get("instance_id", r.get("Instance", ""))) == name],
            "rtu_channels": by_instance("rtu_channels"),
            "options": {"io_channels": copy.deepcopy(state.get("io_channels", [])), "tcp_clients": copy.deepcopy(state.get("tcp_clients", [])), "rtu_lines": copy.deepcopy(state.get("rtu_lines", [])), "rtu_slaves": copy.deepcopy(state.get("rtu_slaves", [])),
              "io_members": copy.deepcopy(next((d.get("members",[]) for t in state.get("types",[]) if str(t.get("id"))==str(instance.get("type_id")) or str(t.get("name"))==str(instance.get("type_id")) for d in t.get("duts",[]) if str(d.get("role"))=="IO"),[]))}}


def register_instance_editor_routes(app, get_session, current_user, csrf):
    from backend import v2_store as store

    def project_for(session):
        state = store.load_project(session)
        if state is None: raise HTTPException(503, "The v2 project has not been initialized")
        return state

    @app.get("/api/v2/instances/{instance_id}/configuration")
    def configuration(instance_id: str, session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        inst = next((i for i in state.get("instances", []) if str(i.get("id", i.get("name"))) == instance_id or str(i.get("name")) == instance_id), None)
        if not inst: raise HTTPException(404, "Instance not found")
        return _configuration(state, inst)

    @app.post("/api/v2/instances/preview")
    def preview(body: Preview, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if body.revision != current.get("revision", 0): raise HTTPException(409, "Project revision changed; reload instances")
        state = copy.deepcopy(current); errors=[]; warnings=[]; changes=[]
        try:
            inst = next((i for i in state.get("instances", []) if str(i.get("id", i.get("name"))) == str(body.instance_id) or str(i.get("name")) == str(body.instance_id)), None)
            if body.action == "create":
                typ = _type(state, body.type_id)
                _add_instance(state, (body.name or "").strip(), typ, {})
                changes.append(f"Create instance {(body.name or '').strip()} ({typ.get('name') if typ else body.type_id})")
            elif not inst: raise ValueError("Instance not found")
            elif body.action == "delete":
                name = _name(inst)
                from backend.v2_program_membership import freeze_membership
                freeze_membership(state)
                impact = {k: sum(1 for r in state.get(k, []) if str(r.get("instance_id",r.get("instance",r.get("Instance","")))) == name) for k in ("pa_values","io_channels","tcp_points","rtu_channels")}
                state["instances"] = [i for i in state["instances"] if _name(i) != name]
                for key in impact:
                    if key=="io_channels":
                        for row in state.get(key,[]):
                            if str(row.get("instance_id",row.get("Instance","")))==name:
                                prior=row.pop("_assignment_prior_status",None)
                                for field in ("Instance","instance_id","GVL_IO member","member_name"):
                                    row[field]=None
                                if prior is not None: row["Status"]=prior
                    else: state[key] = [r for r in state.get(key,[]) if str(r.get("instance_id",r.get("instance",r.get("Instance","")))) != name]
                state["rtu_slaves"] = [r for r in state.get("rtu_slaves",[]) if str(r.get("instance_id",r.get("Instance",""))) != name]
                _clear_virtual_io_for_instance(state, name)
                _remove_program_membership(state, name)
                state["exchange_calls"] = [r for r in state.get("exchange_calls",[]) if name not in {str(r.get("instance_a_id") or r.get("Instance A")),str(r.get("instance_b_id") or r.get("Instance B"))}]
                state.get("exchange_layout", {}).get("nodes", {}).pop(name, None)
                changes.append(f"Delete {name}; release {impact['tcp_points']} TCP points, {impact['rtu_channels']} RTU channels, {impact['io_channels']} I/O assignments, and {impact['pa_values']} PA values")
            elif body.action == "pa":
                existing_rows=[x for x in state.get("pa_values",[]) if str(x.get("instance_id",x.get("instance",x.get("Instance"))))==_name(inst)]
                saved_by_member={str(x.get("Member",x.get("member_name"))):x for x in existing_rows}
                by_member={str(x.get("Member",x.get("member_name"))):x for x in _pa_editor_rows(state, inst)}
                for member,value in body.values.items():
                    if member not in by_member: raise ValueError(f"Unknown PA member {member}")
                    kind=str(by_member[member].get("Type",by_member[member].get("data_type",""))).upper()
                    if value not in (None,""):
                        raw=str(value).strip()
                        try:
                            if kind=="BOOL":
                                if raw.casefold() not in {"true","false","1","0"}: raise ValueError()
                            elif kind in {"REAL","LREAL"}:
                                import math
                                if not math.isfinite(float(raw)): raise ValueError()
                            else:
                                parsed=int(raw,0) if raw.lower().startswith("0x") else int(raw)
                                bounds={"BYTE":(0,255),"USINT":(0,255),"SINT":(-128,127),"WORD":(0,65535),"UINT":(0,65535),"INT":(-32768,32767),"DWORD":(0,4294967295),"UDINT":(0,4294967295),"DINT":(-2147483648,2147483647)}
                                if kind in bounds and not bounds[kind][0]<=parsed<=bounds[kind][1]: raise ValueError()
                        except (ValueError,OverflowError): raise ValueError(f"{member} must be a valid {kind} value")
                    row=saved_by_member.get(member)
                    if row is None:
                        row=by_member[member]
                        row=copy.deepcopy(row)
                        state.setdefault("pa_values",[]).append(row)
                        saved_by_member[member]=row
                    row["Instance value"]=value if value not in ("",None) else None; row["value"]=value if value not in ("",None) else None
                changes.extend([f"{_name(inst)}.{m}: {v}" for m,v in body.values.items()])
            elif body.action == "io":
                chosen = body.io_channels
                seen=set()
                type_obj=_type(state,inst.get("type_id")); io_dut=next((d for d in (type_obj or {}).get("duts",[]) if d.get("role")=="IO"),{})
                members={str(m.get("name")):m for m in io_dut.get("members",[])}
                free={str(r.get("id")):r for r in state.get("io_channels",[]) if not r.get("Instance") and not r.get("instance_id")}
                all_channels={str(r.get("id")):r for r in state.get("io_channels",[]) if str(r.get("instance_id",r.get("Instance","")))==_name(inst)}
                assigned_members=set()
                for c in chosen:
                    cid=str(c.get("id", ""))
                    if cid in seen: raise ValueError("An I/O channel was selected more than once")
                    seen.add(cid)
                    row=free.get(cid) or all_channels.get(cid)
                    if row is None: raise ValueError(f"I/O channel {cid} is not available")
                    member=str(c.get("member", ""))
                    if member not in members: raise ValueError(f"Choose a DUT_IO member for channel {cid}")
                    if member in assigned_members: raise ValueError(f"DUT_IO member {member} can only map to one channel")
                    assigned_members.add(member)
                    io_type=str(row.get("I/O type",row.get("io_type",""))).upper()
                    iec_type=str(members[member].get("iec_type","")).upper()
                    if io_type.startswith("A") and not any(x in iec_type for x in ("WORD","DWORD","INT","REAL")): raise ValueError(f"{member} is not compatible with {io_type} channel {cid}")
                    if io_type.startswith("D") and "BOOL" not in iec_type: raise ValueError(f"{member} is not compatible with {io_type} channel {cid}")
                    row["Instance"]=_name(inst);row["instance_id"]=_name(inst)
                    row.setdefault("_assignment_prior_status",row.get("Status"))
                    row["GVL_IO member"]=f"GVL_IO.IO_{_name(inst)}.{member}";row["member_name"]=member;row["IOMAP type"]=row.get("IOMAP type") or members[member].get("iec_type")
                    existing_map=str(row.get("GVL_IOMAP variable") or row.get("variable") or "")
                    match=re.search(r"IOMAP_[A-Z]+_(.+?)(?:_[A-Z][A-Z0-9_]*_\d+)?$",existing_map)
                    signal=match.group(1) if match else re.sub(r"^(INPUT|OUTPUT)_","",member)
                    row["GVL_IOMAP variable"]=existing_map or f"GVL_IOMAP.IOMAP_{io_type}_{signal}_{_name(inst)}";row["variable"]=row["GVL_IOMAP variable"]
                    row["Status"]="TO CHECK"
                for cid,row in all_channels.items():
                    if cid not in seen:
                        prior=row.pop("_assignment_prior_status",None)
                        for field in ("Instance","instance_id","GVL_IO member","member_name"):
                            row[field]=None
                        if prior is not None: row["Status"]=prior
                changes.append(f"Assign {len(chosen)} physical I/O channels to {_name(inst)}")
            elif body.action == "modbus":
                for field, rows_key, field_names in (("tcp_points","tcp_points",("Modbus address","address")),("rtu_slaves","rtu_slaves",("Slave address","address"))):
                    edits=body.tcp_points if field=="tcp_points" else body.rtu_slaves
                    pool=[r for r in state.get(rows_key,[]) if str(r.get("instance_id",r.get("Instance","")))==_name(inst)]
                    external=[r for r in state.get(rows_key,[]) if r not in pool]
                    for edit in edits:
                        row=next((r for r in pool if str(r.get("id"))==str(edit.get("id"))),None)
                        if row is None: raise ValueError(f"Unknown {field} row")
                        v=edit.get("address",edit.get(field_names[0]))
                        if field=="rtu_slaves" and v in (None,"","TO CHECK") and str(row.get("Slave address",row.get("address")))=="TO CHECK": continue
                        try: n=int(v)
                        except (TypeError,ValueError): raise ValueError("Modbus addresses must be whole numbers")
                        if field=="tcp_points":
                            lo,hi=_tcp_range(row.get("Client",row.get("client_id","FUXA")),row.get("table",row.get("sheet")),state)
                            if not lo<=n<=hi: raise ValueError(f"TCP address must be between {lo} and {hi}")
                        if field=="rtu_slaves" and not 1<=n<=247: raise ValueError("RTU slave address must be between 1 and 247")
                        same=[]
                        for other in external+pool:
                            if other is row or str(other.get("table", ""))!=str(row.get("table", "")): continue
                            try: other_address=int(other.get("Modbus address",other.get("address",-1)))
                            except (TypeError,ValueError): continue
                            if other_address==n and (field!="rtu_slaves" or str(other.get("Line",other.get("line_id","")))==str(row.get("Line",row.get("line_id","")))): same.append(other)
                        if same: raise ValueError(f"Modbus address {n} is already in use")
                        for key in field_names:
                            if key in row: row[key]=n
                        row["address"]=n
                        if field=="tcp_points":
                            from backend.v2_addressing import client_address_for
                            row["Client address"]=client_address_for(row.get("table"),n,row.get("Client"))
                            row["GVL_MODBUS variable"]=_tcp_variable(row.get("table",row.get("sheet")),n)
                for edit in body.rtu_slaves:
                    row=next((r for r in state.get("rtu_slaves",[]) if str(r.get("id"))==str(edit.get("id"))),None)
                    if row:
                        val=edit.get("address",edit.get("Slave address"))
                        if val in (None,"", "TO CHECK") and str(row.get("Slave address",row.get("address")))=="TO CHECK": continue
                        row["Slave address"]=int(val); row["address"]=int(val)
                changes.append(f"Update Modbus addresses for {_name(inst)}")
            else: raise ValueError("Unsupported instance action")
        except ValueError as exc: errors.append(str(exc))
        if errors: return {"token":None,"errors":errors,"warnings":warnings,"changes":changes}
        if body.action != "create":
            _sync_workbooks(state)
        if body.action != "create":
            from backend.v2_engine import validate_project
            baseline_errors=set(validate_project(current).get("errors",[]))
            validation=validate_project(state)
            added_errors=[e for e in validation.get("errors",[]) if e not in baseline_errors]
            if added_errors: return {"token":None,"errors":added_errors,"warnings":validation.get("warnings",[]),"changes":changes}
            warnings.extend(validation.get("warnings",[]))
        token=secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token,owner_id=user.id,state_payload=json.dumps(state,ensure_ascii=False),diff_payload=json.dumps({"kind":"v2-instance-edit","action":body.action,"base_revision":current.get("revision",0)},ensure_ascii=False),expires_at=datetime.now(timezone.utc)+timedelta(hours=2)))
        session.commit()
        return {"token":token,"errors":errors,"warnings":warnings,"changes":changes,"configuration": _configuration(state, next((i for i in state.get("instances",[]) if _name(i)==(body.name or body.instance_id)),{})) if body.action!="delete" else None}

    @app.post("/api/v2/instances/apply")
    def apply(body: Apply, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token); now=datetime.now(timezone.utc)
        if not preview or preview.owner_id!=user.id: raise HTTPException(404,"Instance preview not found or expired")
        expiry=preview.expires_at.replace(tzinfo=timezone.utc) if preview.expires_at.tzinfo is None else preview.expires_at
        if expiry<=now: raise HTTPException(404,"Instance preview not found or expired")
        metadata=json.loads(preview.diff_payload)
        if metadata.get("kind")!="v2-instance-edit": raise HTTPException(404,"Instance preview not found or expired")
        current=project_for(session)
        if current.get("revision",0)!=metadata.get("base_revision"): raise HTTPException(409,"Project changed after preview; create a new preview")
        state=json.loads(preview.state_payload)
        if metadata.get("action") != "create":
            _sync_workbooks(state)
        try: saved=store.save_project(session,state,username=user.username,expected_revision=metadata["base_revision"])
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        session.delete(preview); session.commit()
        return {"revision":saved["revision"]}
