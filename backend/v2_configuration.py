"""Reviewed, revisioned edits for the complete V2 configuration."""
from __future__ import annotations

import copy
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from backend.db import ImportPreview
from backend.v2_engine import _sheet_map

COLLECTIONS = ("tcp_clients", "tcp_points", "rtu_lines", "rtu_slaves", "rtu_channels", "io_modules", "io_channels", "pa_values")
EDITABLE = {
    "tcp_clients": {"Description", "Role", "IP address", "Comment", "Word order (32-bit)", "description", "role", "ip_address", "comment", "word_order"},
    "tcp_points": {"Modbus address", "Client address", "modbus_address", "client_address"},
    "rtu_lines": {"PLC port", "Baud rate", "Parity", "Data bits", "Stop bits", "Response timeout (ms)", "port", "baud_rate", "parity", "data_bits", "stop_bits", "timeout_ms"},
    "rtu_slaves": {"Line", "Slave address", "CODESYS slave device", "32-bit word order", "Comment", "line_id", "address", "device", "word_order", "comment"},
    "rtu_channels": {"Name", "Equipment address", "Scale", "Unit", "CODESYS channel", "Comment", "name", "address", "scale", "unit", "codesys_channel", "comment"},
    "io_modules": {"Description", "Status", "CODESYS device", "description", "status", "device"},
    "io_channels": {"Instance", "GVL_IO member", "GVL_IOMAP variable", "IOMAP type", "Signal", "Comment", "Instance value", "Range min", "Range max", "Scale min", "Scale max", "Unit", "instance_id", "member_name", "variable", "signal", "comment", "scale_min", "scale_max", "unit"},
    "pa_values": {"Instance value", "Changed from supervision", "value", "supervision_editable"},
}
ALIASES = {"description":"Description", "role":"Role", "ip_address":"IP address", "comment":"Comment", "word_order":"Word order (32-bit)", "modbus_address":"Modbus address", "client_address":"Client address", "port":"PLC port", "baud_rate":"Baud rate", "data_bits":"Data bits", "stop_bits":"Stop bits", "timeout_ms":"Response timeout (ms)", "line_id":"Line", "address":"Equipment address", "device":"CODESYS slave device", "scale":"Scale", "unit":"Unit", "codesys_channel":"CODESYS channel", "status":"Status", "instance_id":"Instance", "member_name":"GVL_IO member", "variable":"GVL_IOMAP variable", "signal":"Signal", "scale_min":"Range min", "scale_max":"Range max", "value":"Instance value", "supervision_editable":"Changed from supervision"}
IEC_INTEGER_BOUNDS={"SINT":(-128,127),"USINT":(0,255),"BYTE":(0,255),"INT":(-32768,32767),"UINT":(0,65535),"WORD":(0,65535),"DINT":(-(2**31),2**31-1),"UDINT":(0,2**32-1),"DWORD":(0,2**32-1),"LINT":(-(2**63),2**63-1),"ULINT":(0,2**64-1),"LWORD":(0,2**64-1)}


def _ident(row):
    return str(row.get("id", row.get("Client", row.get("Instance", row.get("Slot", "")))))


def _num(value, label, integer=False):
    if isinstance(value, bool): raise ValueError(f"{label} must be numeric")
    try:
        if integer:
            numeric = Decimal(str(value).strip())
            if not numeric.is_finite() or numeric != numeric.to_integral_value(): raise ValueError
            n = int(numeric)
        else: n = float(value)
    except (TypeError, ValueError, OverflowError, InvalidOperation): raise ValueError(f"{label} must be {'a whole number' if integer else 'numeric'}")
    if not math.isfinite(float(n)): raise ValueError(f"{label} must be finite")
    return n


def _type_for(state, instance):
    iid = str(instance.get("id", instance.get("name", "")))
    sid = str(instance.get("type_id", ""))
    return next((t for t in state.get("types", []) if str(t.get("id")) == sid or str(t.get("name")) == sid), None)


def apply_changes(state, changes):
    """Apply declarative edits to a copy; return warnings and required manual work."""
    warnings, manual = [], []
    for change in changes:
        coll, op = change.get("collection"), change.get("op")
        if coll not in COLLECTIONS or op not in {"update", "create", "delete"}: raise ValueError("Unknown collection or operation")
        rows = state.setdefault(coll, []); ident = str(change.get("id", "")); values = change.get("values") or {}
        if not isinstance(values, dict): raise ValueError("values must be an object")
        row = next((r for r in rows if _ident(r) == ident), None) if ident else None
        if op == "create":
            if coll not in {"tcp_clients", "rtu_slaves"}: raise ValueError(f"Creating {coll} rows is not supported")
            row = copy.deepcopy(values)
            row.setdefault("id", ident or row.get("Client") or row.get("Instance"))
            if not row.get("id"): raise ValueError("A stable id is required")
            if any(_ident(r).casefold() == str(row["id"]).casefold() for r in rows): raise ValueError("That id already exists")
            if coll=="tcp_clients":
                client_name=str(row.get("Client",row["id"])).strip()
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*",client_name): raise ValueError("TCP client id must be a valid IEC identifier")
                if any(str(r.get("Client",r.get("id",""))).casefold()==client_name.casefold() for r in rows): raise ValueError("That TCP client already exists")
                row.update(id=client_name,name=client_name,Client=client_name)
            if coll == "rtu_slaves":
                instance=next((i for i in state.get("instances",[]) if str(i.get("id",i.get("name")))==str(row.get("Instance", ""))),None)
                if not instance: raise ValueError("RTU slave must reference an existing instance")
                typ=_type_for(state,instance)
                row.update(id=str(row.get("Instance")),name=str(row.get("Instance")),instance_id=str(row.get("Instance")),type_id=instance.get("type_id"),**{"Object type":row.get("Object type",(typ or {}).get("name")),"Slave address":row.get("Slave address",row.get("address")),"Line":row.get("Line",row.get("line_id")),"CODESYS slave device":row.get("CODESYS slave device",row.get("device"))})
            rows.append(row)
            if coll=="tcp_clients":
                ranges=row.get("ranges")
                if not isinstance(ranges,dict): raise ValueError("Creating a TCP client requires ranges for all four tables")
                from backend.v2_addressing import set_client_ranges
                set_client_ranges(state,row["Client"],ranges); _refresh_aliases(row,coll)
            else: _refresh_aliases(row,coll)
            manual.append(f"Configure the new {coll[:-1]} in CODESYS and the physical device, then verify communication.")
            continue
        if not row: raise ValueError(f"{coll} row {ident} was not found")
        had_io_mapping = bool(row.get("Instance") or row.get("GVL_IOMAP variable")) if coll == "io_channels" else False
        if op == "delete":
            if coll not in {"tcp_clients","tcp_points","rtu_slaves"}: raise ValueError(f"Deleting {coll} rows is not supported")
            if coll == "tcp_clients" and any(str(p.get("Client", p.get("client_id", ""))) == ident for p in state.get("tcp_points", [])): raise ValueError("Cannot delete a TCP client that still has points")
            if coll == "rtu_slaves" and any(str(c.get("Instance", c.get("instance_id", ""))) == ident for c in state.get("rtu_channels", [])): raise ValueError("Cannot delete an RTU slave that still has channels")
            rows.remove(row)
            manual.append(f"Remove the deleted {coll[:-1]} from the corresponding CODESYS device configuration.")
            if coll=="tcp_points": manual.append(f"Remove TCP point {ident} from every connected client configuration.")
            continue
        allowed = EDITABLE[coll]
        if coll == "tcp_clients" and "ranges" in values:
            try:
                from backend.v2_addressing import set_client_ranges
                set_client_ranges(state, ident, values["ranges"])
            except ImportError as exc: raise ValueError("TCP range editing is temporarily unavailable") from exc
        for key, value in values.items():
            if coll == "tcp_clients" and key == "ranges": continue
            if key not in allowed: raise ValueError(f"{key} is read-only for {coll}")
            canonical = ALIASES.get(key, key)
            if key == "address": canonical = "Slave address" if coll == "rtu_slaves" else "Equipment address"
            row[canonical] = value
        if coll == "pa_values":
            dtype = str(row.get("Type", row.get("data_type", ""))).upper(); val = row.get("Instance value")
            if val not in (None, ""):
                if dtype in {"BOOL", "BOOLEAN"}:
                    if isinstance(val, str) and val.strip().lower() not in {"true", "false", "0", "1"}: raise ValueError(f"{row.get('Member')} must be a valid BOOL")
                    if val not in (0, 1, True, False, "0", "1", "TRUE", "FALSE", "true", "false"): raise ValueError(f"{row.get('Member')} must be a valid BOOL")
                elif dtype in {"WORD", "UINT"}: 
                    n = _num(val, f"{row.get('Member')} (WORD)", True)
                    if not 0 <= n <= 65535: raise ValueError(f"{row.get('Member')} must be a valid WORD (0..65535)")
                elif dtype in {"INT"}:
                    n = _num(val, f"{row.get('Member')} (INT)", True)
                    if not -32768 <= n <= 32767: raise ValueError(f"{row.get('Member')} must be a valid INT (-32768..32767)")
                elif dtype in {"REAL", "LREAL"}: _num(val, f"{row.get('Member')} (REAL)")
            if "Changed from supervision" in values or "supervision_editable" in values:
                metadata = row.get("Changed from supervision")
                if str(metadata).casefold() not in {"yes", "no", "true", "false", ""}: raise ValueError("Changed from supervision must be Yes or No")
        elif coll == "tcp_points":
            addr = _num(row.get("Modbus address", row.get("address")), "Modbus address", True)
            row["Modbus address"] = row["address"] = addr
            try:
                from backend.v2_addressing import client_address_for
                row["Client address"] = client_address_for(str(row.get("table", row.get("sheet"))).casefold(), addr, row.get("Client",row.get("client_id")))
            except ImportError: row["Client address"] = addr
            try:
                from backend.v2_instance_editor import _tcp_variable
                row["GVL_MODBUS variable"] = _tcp_variable(row.get("table", row.get("sheet")), addr)
            except ImportError: pass
            manual.append(f"Update and verify TCP point {ident} at address {addr} in the client configuration.")
        elif coll == "rtu_lines":
            for key, choices in (("Baud rate", {1200,2400,4800,9600,19200,38400,57600,115200}), ("Data bits", {7,8}), ("Stop bits", {1,2})):
                value=row.get(key)
                if value not in (None, "", "TO CHECK") and _num(value,key,True) not in choices: raise ValueError(f"{key} is not a supported value")
            if str(row.get("Parity", "")).upper() not in {"", "TO CHECK", "NONE", "EVEN", "ODD", "N", "E", "O"}: raise ValueError("Parity must be None, Even, or Odd")
            manual.append(f"Set and verify serial port settings for RTU line {row.get('Line', ident)} in CODESYS; this preview does not change the PLC port.")
        elif coll == "rtu_slaves":
            value=row.get("Slave address", row.get("address"))
            if value not in (None, "", "TO CHECK"):
                n=_num(value,"RTU slave address",True)
                if not 1 <= n <= 247: raise ValueError("RTU slave address must be between 1 and 247")
                line=str(row.get("Line", ""))
                if any(other is not row and str(other.get("Line", ""))==line and str(other.get("Slave address", ""))==str(value) for other in rows): raise ValueError("RTU slave address is already used on this line")
            if not any(str(i.get("id",i.get("name")))==str(row.get("Instance", "")) for i in state.get("instances", [])): raise ValueError("RTU slave must reference an existing instance")
            manual.append(f"Set and verify RTU slave {ident} address/device in CODESYS and on the physical device.")
        elif coll == "io_channels":
            if ("GVL_IOMAP variable" in values or "variable" in values or "IOMAP type" in values) and had_io_mapping:
                raise ValueError("Mapped I/O variables and types are fixed for existing channels")
            instance_name=str(row.get("Instance", "") or ""); member=str(row.get("GVL_IO member", "") or "")
            if instance_name and member:
                leaf=member.split(".")[-1]
                expected_prefix=f"GVL_IO.IO_{instance_name}."
                if "." in member and not member.startswith(expected_prefix): raise ValueError("GVL_IO member path must belong to the selected instance")
                inst=next((i for i in state.get("instances",[]) if str(i.get("id",i.get("name")))==instance_name),None)
                typ=_type_for(state,inst) if inst else None
                members=[m for d in (typ or {}).get("duts",[]) if str(d.get("role", "")).upper()=="IO" for m in d.get("members",[])]
                found=next((m for m in members if str(m.get("name"))==leaf or str(m.get("id"))==leaf),None)
                if not found: raise ValueError("Choose an existing I/O member on the selected instance")
                canonical=f"GVL_IO.IO_{instance_name}.{leaf}"
                if any(other is not row and str(other.get("Instance",""))==instance_name and str(other.get("GVL_IO member","")) in {leaf,canonical} for other in state.get("io_channels",[])): raise ValueError("This instance member is already assigned to an I/O channel")
                io=str(row.get("I/O type", "")).upper(); ft=str(found.get("iec_type", "")).upper()
                if (io.startswith("D") and ft not in {"BOOL"}) or (io.startswith("A") and ft in {"BOOL"}): raise ValueError("I/O type does not match the selected member type")
                if not row.get("GVL_IOMAP variable") or not row.get("IOMAP type"): raise ValueError("Set the physical GVL_IOMAP variable and type for this channel")
                row["GVL_IO member"]=canonical
            for a,b in (("Range min","Range max"),("Scale min","Scale max")):
                if row.get(a) not in (None, "") and row.get(b) not in (None, "") and _num(row[a],a) >= _num(row[b],b): raise ValueError("Scale min must be less than Scale max")
            if "Instance" in values or "GVL_IO member" in values or "instance_id" in values or "member_name" in values:
                manual.append(f"Verify physical channel {row.get('Slot')}:{row.get('Channel')} wiring and the CODESYS mapping for instance {row.get('Instance') or 'unassigned'}.")
            if ("GVL_IOMAP variable" in values or "variable" in values) and not row.get("Instance"):
                variable=str(row.get("GVL_IOMAP variable", "")).strip()
                if not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*", variable): raise ValueError("New I/O mapping variable must be a valid IEC variable path")
                if any(other is not row and str(other.get("GVL_IOMAP variable", ""))==variable for other in state.get("io_channels", [])): raise ValueError("I/O mapping variable is already used")
                if not row.get("IOMAP type"): raise ValueError("Set an IOMAP type for the new physical channel variable")
                manual.append(f"Add and verify {variable} ({row.get('IOMAP type')}) in the physical I/O mapping and CODESYS device configuration.")
        _refresh_aliases(row, coll)
    result=validate_configuration(state)
    if result["errors"]: raise ValueError(result["errors"][0])
    return list(dict.fromkeys(warnings+result["warnings"])), list(dict.fromkeys(manual))


def _refresh_aliases(row, coll):
    if coll == "tcp_clients":
        for alias, field in (("ip_address","IP address"),("role","Role"),("description","Description"),("address_range","Address range"),("word_order","Word order (32-bit)"),("comment","Comment")):
            if field in row: row[alias]=row[field]
    elif coll == "tcp_points":
        row.update(address=row.get("Modbus address"),client_address=row.get("Client address"),client_id=row.get("Client"),instance_id=row.get("Instance"),member_name=row.get("member_name"),word=row.get("Word"),data_type=row.get("Type"))
    elif coll == "rtu_lines":
        for alias, field in (("port","PLC port"),("baud_rate","Baud rate"),("parity","Parity"),("data_bits","Data bits"),("stop_bits","Stop bits"),("timeout_ms","Response timeout (ms)")):
            if field in row: row[alias]=row[field]
    elif coll == "rtu_slaves":
        row.update(line_id=row.get("Line"),address=row.get("Slave address"),device=row.get("CODESYS slave device"),word_order=row.get("32-bit word order"))
    elif coll == "rtu_channels":
        row.update(address=row.get("Equipment address"),member_name=row.get("member_name"),data_type=row.get("Type"),direction=row.get("Direction"))
    elif coll == "io_modules":
        row.update(slot=row.get("Slot"),device=row.get("CODESYS device"),status=row.get("Status"))
    elif coll == "io_channels":
        from backend.v2_engine import _member_from_var
        row.update(instance_id=row.get("Instance"),variable=row.get("GVL_IOMAP variable"),member_name=_member_from_var(row.get("GVL_IO member")),signal=row.get("Signal"))
    elif coll == "pa_values": row.update(value=row.get("Instance value"),supervision_editable=row.get("Changed from supervision"))


def validate_configuration(state):
    errors, warnings = [], []
    seen=set()
    for p in state.get("tcp_points",[]):
        address=p.get("Modbus address",p.get("address")); table=str(p.get("table",p.get("sheet",""))).casefold()
        if address in (None, "", "TO CHECK"): continue
        try: key=(table,int(address))
        except (ValueError,TypeError): errors.append(f"Invalid TCP address {address}"); continue
        if key in seen: errors.append(f"TCP address {key[1]} is already used in {key[0]}")
        seen.add(key)
    pairs={}
    for p in state.get("tcp_points",[]):
        if str(p.get("Word", "")).upper() in {"LSW","MSW"}:
            key=(str(p.get("Client",p.get("client_id",""))),str(p.get("Instance",p.get("instance_id",""))),str(p.get("Name",p.get("name",""))))
            pairs.setdefault(key,[]).append(p)
    for key, group in pairs.items():
        try: addresses=[int(r.get("Modbus address",r.get("address"))) for r in group]
        except (TypeError,ValueError): errors.append(f"TCP 32-bit pair {key[2]} requires whole-number addresses"); continue
        words={str(r.get("Word", "")).upper() for r in group}
        if len(group)!=2 or words!={"LSW","MSW"} or max(addresses)-min(addresses)!=1:
            errors.append(f"TCP 32-bit pair {key[2]} must contain consecutive LSW/MSW addresses for the same client and instance")
    for p in state.get("tcp_points",[]):
        client=str(p.get("Client",p.get("client_id","")))
        try:
            from backend.v2_addressing import client_ranges
            ranges=client_ranges(state, client)
            table=str(p.get("table",p.get("sheet",""))).casefold(); r=ranges.get(table)
            a=int(p.get("Modbus address",p.get("address")))
            if r and not int(r[0]) <= a <= int(r[1]): errors.append(f"TCP address {a} is outside client {client} {table} range")
        except (ImportError,TypeError,ValueError,KeyError) as exc: errors.append(f"Invalid TCP client ranges for {client}: {exc}")
    for line in state.get("rtu_lines",[]):
        for key, val in (("Baud rate",line.get("Baud rate")),("Data bits",line.get("Data bits")),("Stop bits",line.get("Stop bits"))):
            if val in (None,"","TO CHECK"): continue
            try: _num(val,key,True)
            except ValueError as e: errors.append(str(e))
        port=line.get("PLC port",line.get("port"))
        if port not in (None,"","TO CHECK") and not re.fullmatch(r"COM\d+",str(port).strip(),re.I): errors.append(f"RTU PLC port {port} must be COM<number> or TO CHECK")
        parity=str(line.get("Parity", "")).upper()
        if parity not in {"","TO CHECK","NONE","EVEN","ODD","N","E","O"}: errors.append(f"RTU parity {parity} is not supported")
        timeout=line.get("Response timeout (ms)",line.get("timeout_ms"))
        if timeout not in (None,"","TO CHECK"):
            try:
                timeout_num=_num(timeout,"Response timeout (ms)")
                if not 1<=timeout_num<=60000: errors.append("Response timeout (ms) must be between 1 and 60000")
            except ValueError as e: errors.append(str(e))
    slave_addresses=set()
    for row in state.get("rtu_slaves",[]):
        addr=row.get("Slave address",row.get("address"))
        if addr in (None,"","TO CHECK"): continue
        try: addr=int(addr)
        except (TypeError,ValueError): errors.append(f"Invalid RTU slave address {addr}"); continue
        key=(str(row.get("Line",row.get("line_id",""))),addr)
        if key in slave_addresses: errors.append(f"RTU slave address {addr} is already used on line {key[0]}")
        slave_addresses.add(key)
    for row in state.get("pa_values",[]):
        val=row.get("Instance value",row.get("value")); dtype=str(row.get("Type",row.get("data_type",""))).upper()
        if val in (None,""): continue
        label=str(row.get("Member",row.get("member_name","PA value")))
        try:
            if dtype=="BOOL":
                if str(val).strip().casefold() not in {"true","false","0","1"}: raise ValueError
            elif dtype in IEC_INTEGER_BOUNDS:
                n=_num(val,label,True); lo,hi=IEC_INTEGER_BOUNDS[dtype]
                if not lo<=n<=hi: raise ValueError
            elif dtype=="REAL":
                n=_num(val,label)
                if abs(n)>3.4028235e38: raise ValueError
            elif dtype=="LREAL": _num(val,label)
        except (ValueError,TypeError): errors.append(f"{label} has an invalid {dtype} value")
    channels={}
    for row in state.get("rtu_channels",[]):
        raw=row.get("Equipment address",row.get("address"))
        if raw in (None,""): continue
        try:
            number=int(str(raw),16) if str(raw).lower().startswith("0x") else int(raw)
            if number<0: raise ValueError
        except (TypeError,ValueError): errors.append(f"Invalid RTU equipment address {raw}"); continue
        # A base byte may legitimately contain several bit channels. Detect duplicate
        # byte/word mappings by full mapped path and offset, while permitting bit peers.
        path=str(row.get("GVL_MODBUS variable", "")); offset=str(row.get("Channel offset",row.get("channel_offset","")))
        key=(str(row.get("Instance",row.get("instance_id",""))),path or number,offset if offset not in ("",None) else number)
        if key in channels and str(row.get("Type", "")).upper()!="BOOL": errors.append(f"RTU mapped address {raw} overlaps another non-bit channel")
        channels[key]=row
    return {"errors":list(dict.fromkeys(errors)),"warnings":warnings}


def _capacity_warnings(state):
    try:
        from backend.v2_capacity import tcp_capacity
        capacity=tcp_capacity(state)
    except (ImportError,TypeError,ValueError,KeyError): return []
    over=[]
    for row in state.get("tcp_points",[]):
        table=str(row.get("table",row.get("sheet",""))).casefold()
        try: address=int(row.get("Modbus address",row.get("address")))
        except (TypeError,ValueError): continue
        bound=capacity.get(table)
        if bound and not int(bound[0])<=address<=int(bound[1]): over.append((table,address))
    if not over: return []
    tables=", ".join(sorted({t for t,_ in over}))
    return [f"{len(over)} TCP point(s) exceed the current {tables} PLC/XML capacity. Export validation will block until the CODESYS GVL_MODBUS/server ranges are resized, refreshed XML is imported, and the declarations are regenerated."]


def _sync_rows(state, collection, workbook, sheet_names, keyfn):
    if not workbook: return
    sheets=_sheet_map(workbook)
    for sheet_name in sheet_names:
        sh=sheets.get(sheet_name)
        if not sh: continue
        headers=list(map(str,sh.get("headers",[]))); old=[]
        for vals in sh.get("rows",[]): old.append(dict(zip(headers,vals)))
        current=[r for r in state.get(collection,[]) if keyfn(r)==sheet_name]
        bykey={keyfn(r)+"|"+str(r.get("id",r.get("Instance",r.get("Client",r.get("Member",""))))):r for r in old}
        # Match original workbook rows to normalized state by stable workbook fields.
        output=[]
        for row in current:
            candidate=next((o for o in old if all(o.get(k)==row.get(k) for k in ("Instance","Client","Member","Table","Equipment address","Slot","Channel") if k in row and k in o)),{})
            merged={**candidate,**row}; output.append([merged.get(h) for h in headers])
        sh["rows"]=output


def synchronize_workbooks(state):
    """Patch only supported sheets, preserving other workbooks and workbook metadata."""
    books=state.get("workbooks",{})
    tcp=books.get("MODBUS_TCP.xlsx")
    if tcp:
        clients=_sheet_map(tcp).get("CLIENTS")
        if clients:
            headers=clients.get("headers",[]); old=[dict(zip(headers,row)) for row in clients.get("rows",[])]; out=[]
            for row in state.get("tcp_clients",[]):
                prior=next((o for o in old if str(o.get("Client"))==str(row.get("Client",row.get("id")))),{})
                out.append([{**prior,**row}.get(h) for h in headers])
            clients["rows"]=out
        for sh in ("COILS","DISCRETE_INPUTS","INPUT_REGISTERS","HOLDING_REGISTERS"):
            sheet=_sheet_map(tcp).get(sh)
            if sheet:
                headers=sheet.get("headers",[]); old=[dict(zip(headers,row)) for row in sheet.get("rows",[])]; output=[]
                for row in state.get("tcp_points",[]):
                    if str(row.get("sheet","")).upper()!=sh: continue
                    original=next((o for o in old if str(o.get("id",""))==str(row.get("id","")) or (str(o.get("Client"))==str(row.get("Client")) and str(o.get("Instance"))==str(row.get("Instance")) and str(o.get("Name"))==str(row.get("Name")))),{})
                    output.append([{**original,**row}.get(h) for h in headers])
                sheet["rows"]=output
    rtu=books.get("MODBUS_RTU.xlsx")
    if rtu:
        for sheet, coll, key in (("LINES","rtu_lines","Line"),("SLAVES","rtu_slaves","Instance")):
            sh=_sheet_map(rtu).get(sheet)
            if sh:
                headers=sh.get("headers",[]); old=[dict(zip(headers,row)) for row in sh.get("rows",[])]; out=[]
                for row in state.get(coll,[]):
                    prior=next((o for o in old if str(o.get(key))==str(row.get(key))),{})
                    out.append([{**prior,**row}.get(h) for h in headers])
                sh["rows"]=out
        for sh in rtu.get("sheets",[]):
            name=sh.get("name")
            if name in {"README","LINES","SLAVES","LISTS"}: continue
            headers=sh.get("headers",[]); old=[dict(zip(headers,row)) for row in sh.get("rows",[])]; out=[]
            for row in state.get("rtu_channels",[]):
                if str(row.get("instance",row.get("instance_id")))!=str(name): continue
                prior=next((o for o in old if str(o.get("Table"))==str(row.get("Table")) and str(o.get("Equipment address"))==str(row.get("Equipment address"))),{})
                out.append([{**prior,**row}.get(h) for h in headers])
            sh["rows"]=out
    io=books.get("IO_PHYSICAL.xlsx")
    if io:
        rack=_sheet_map(io).get("RACK")
        if rack:
            headers=rack.get("headers",[]); old=[dict(zip(headers,row)) for row in rack.get("rows",[])]; out=[]
            for row in state.get("io_modules",[]):
                prior=next((o for o in old if str(o.get("Slot"))==str(row.get("Slot"))),{})
                out.append([{**prior,**row}.get(h) for h in headers])
            rack["rows"]=out
        sh=_sheet_map(io).get("IO")
        if sh:
            headers=sh.get("headers",[]); old=[dict(zip(headers,row)) for row in sh.get("rows",[])]; out=[]
            for row in state.get("io_channels",[]):
                prior=next((o for o in old if str(o.get("Slot"))==str(row.get("Slot")) and str(o.get("Channel"))==str(row.get("Channel"))),{})
                out.append([{**prior,**row}.get(h) for h in headers])
            sh["rows"]=out
    pa=books.get("PARAMETERS_PA.xlsx")
    if pa:
        for sh in pa.get("sheets",[]):
            name=sh.get("name")
            if name in {"README","LISTS"}: continue
            headers=sh.get("headers",[]); old=[dict(zip(headers,row)) for row in sh.get("rows",[])]; out=[]
            for row in state.get("pa_values",[]):
                if str(row.get("instance",row.get("Instance")))!=str(name): continue
                prior=next((o for o in old if str(o.get("Member"))==str(row.get("Member"))),{})
                out.append([{**prior,**row}.get(h) for h in headers])
            sh["rows"]=out
    # Keep export-side variable discovery in step with a deliberately added I/O mapping.
    try:
        from backend.v2_engine import _mapped_vars
        state["mapped_variables"] = _mapped_vars(state)
    except (ImportError, TypeError, ValueError, KeyError):
        pass


def _get_configuration(state):
    result={"revision":state.get("revision",0)}
    for coll in COLLECTIONS: result[coll]=copy.deepcopy(state.get(coll,[]))
    for coll, key in (("io_modules","modules"),):
        if key in state: result[coll]=copy.deepcopy(state[key])
    for client in result["tcp_clients"]:
        try:
            from backend.v2_addressing import client_ranges
            client["ranges"]=client_ranges(state,str(client.get("Client",client.get("id",""))))
        except (ImportError,TypeError,ValueError,KeyError): client["ranges"]={}
    result["instances"]=copy.deepcopy(state.get("instances",[])); result["types"]=copy.deepcopy(state.get("types",[]))
    result["options"]={"lists":{},"io_members":[]}
    for filename,book in state.get("workbooks",{}).items():
        sh=_sheet_map(book).get("LISTS")
        if sh: result["options"]["lists"][filename]=copy.deepcopy(sh.get("rows",[]))
    result["schemas"]={k:[{"name":h,"editable":h in EDITABLE[k]} for h in sorted({str(x) for r in result[k] for x in r if not x.startswith("_")})] for k in COLLECTIONS}
    try:
        from backend.v2_capacity import tcp_capacity
        result["tcp_capacity"]=tcp_capacity(state)
    except (ImportError,TypeError,ValueError,KeyError): result["tcp_capacity"]={}
    return result


class Preview(BaseModel):
    revision:int
    changes:list[dict]
    allocate_tcp:dict|None=None

class Apply(BaseModel): token:str


def register_configuration_routes(app,get_session,current_user,csrf):
    from backend import v2_store as store
    from backend import v2_engine
    def project(session):
        state=store.load_project(session)
        if state is None: raise HTTPException(503,"The v2 project has not been initialized")
        return state
    @app.get("/api/v2/configuration")
    def get_configuration(session=Depends(get_session),user=Depends(current_user)):
        return _get_configuration(project(session))
    @app.post("/api/v2/configuration/preview")
    def preview(body:Preview,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        current=project(session)
        if body.revision!=current.get("revision",0): raise HTTPException(409,"Project revision changed; reload configuration")
        state=copy.deepcopy(current); changes=[]
        try:
            warnings,manual=[],[]
            if body.allocate_tcp:
                from backend.v2_addressing import allocate_tcp_points
                alloc=body.allocate_tcp; added=allocate_tcp_points(state,str(alloc["instance_id"]),str(alloc["client_id"]))
                changes.append(f"Allocate {len(added)} TCP points for {alloc['instance_id']}")
                manual.append("Verify allocated TCP points and client ranges in CODESYS and the SCADA client.")
                edits={str(k):_num(v,"TCP address",True) for k,v in (alloc.get("addresses") or {}).items()}
                for row in added:
                    if str(row.get("id")) not in edits: continue
                    address=edits[str(row["id"])]
                    row["Modbus address"]=row["address"]=address
                    from backend.v2_addressing import client_address_for
                    row["Client address"]=client_address_for(row.get("table"),address,row.get("Client",row.get("client_id")))
                    from backend.v2_instance_editor import _tcp_variable
                    row["GVL_MODBUS variable"]=_tcp_variable(row.get("table"),address)
                if set(edits)-{str(p.get("id")) for p in added}: raise ValueError("Allocation address edits must reference points in this allocation")
            if body.changes:
                extra_warnings,extra_manual=apply_changes(state,body.changes)
                warnings.extend(extra_warnings); manual.extend(extra_manual)
            changes.extend(f"{c['op']} {c['collection']} {c.get('id','')}: {c.get('values',{})}" for c in body.changes)
            synchronize_workbooks(state)
            validation=v2_engine.validate_project(state); baseline=set(v2_engine.validate_project(current).get("errors",[]))
            errors=[e for e in validation.get("errors",[]) if e not in baseline]
            errors.extend(e for e in validate_configuration(state)["errors"] if e not in errors)
            warnings.extend(validation.get("warnings",[]))
            capacity_notes=_capacity_warnings(state)
            warnings.extend(capacity_notes)
            if capacity_notes: manual.append("Resize the CODESYS GVL_MODBUS/server ranges, import refreshed XML references, and regenerate declarations before exporting or deploying these points.")
            if errors: return {"token":None,"errors":list(dict.fromkeys(errors)),"warnings":list(dict.fromkeys(warnings)),"changes":changes,"manual_actions":manual}
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            return {"token":None,"errors":[str(exc)],"warnings":[],"changes":changes,"manual_actions":[]}
        token=secrets.token_urlsafe(32); session.add(ImportPreview(token=token,owner_id=user.id,state_payload=json.dumps(state,ensure_ascii=False),diff_payload=json.dumps({"kind":"v2-configuration","base_revision":current.get("revision",0)}),expires_at=datetime.now(timezone.utc)+timedelta(hours=2)));session.commit()
        return {"token":token,"errors":[],"warnings":list(dict.fromkeys(warnings)),"changes":changes,"manual_actions":list(dict.fromkeys(manual)),"configuration":_get_configuration(state)}
    @app.post("/api/v2/configuration/apply")
    def apply(body:Apply,session=Depends(get_session),user=Depends(current_user),_=Depends(csrf)):
        p=session.get(ImportPreview,body.token); now=datetime.now(timezone.utc)
        if not p or p.owner_id!=user.id: raise HTTPException(404,"Configuration preview not found or expired")
        expiry=p.expires_at.replace(tzinfo=timezone.utc) if p.expires_at.tzinfo is None else p.expires_at
        if expiry<=now: raise HTTPException(404,"Configuration preview not found or expired")
        meta=json.loads(p.diff_payload)
        if meta.get("kind")!="v2-configuration": raise HTTPException(404,"Configuration preview not found or expired")
        current=project(session)
        if current.get("revision",0)!=meta.get("base_revision"): raise HTTPException(409,"Project changed after preview; create a new preview")
        state=json.loads(p.state_payload); synchronize_workbooks(state)
        try: saved=store.save_project(session,state,username=user.username,expected_revision=meta["base_revision"])
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
        session.delete(p);session.commit();return {"revision":saved["revision"]}
