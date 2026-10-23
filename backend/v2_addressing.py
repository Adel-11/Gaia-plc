"""TCP client ranges and collision-safe allocation for Generator v2."""
from __future__ import annotations

import copy
import re

TABLES = ("coils", "discrete_inputs", "input_registers", "holding_registers")
LABELS = {"coils": ("Coils", "COILS"), "discrete_inputs": ("DI", "DISCRETE_INPUTS", "Discrete inputs"),
          "input_registers": ("IR", "INPUT_REGISTERS", "Input registers"), "holding_registers": ("HR", "HOLDING_REGISTERS", "Holding registers")}
BASES = {"coils": 1, "discrete_inputs": 10001, "input_registers": 1, "holding_registers": 1}


def _client(state, client_id):
    return next((c for c in state.get("tcp_clients", []) if str(c.get("Client", c.get("id", c.get("name", "")))).casefold() == str(client_id).casefold()), None)


def _pair(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        if any(isinstance(x, bool) or isinstance(x, float) for x in value): raise ValueError(f"Invalid TCP address range: {value}")
        lo, hi = int(value[0]), int(value[1])
    elif isinstance(value, dict):
        rawlo, rawhi = value.get("lo", value.get("start")), value.get("hi", value.get("end"))
        if any(isinstance(x, (bool,float)) for x in (rawlo,rawhi)): raise ValueError(f"Invalid TCP address range: {value}")
        lo, hi = int(rawlo), int(rawhi)
    else:
        match = re.search(r"(-?\d+)\s*(?:-|\.\.|to)\s*(-?\d+)", str(value), re.I)
        if not match: raise ValueError(f"Invalid TCP address range: {value}")
        lo, hi = int(match.group(1)), int(match.group(2))
    if lo < 0 or hi < lo: raise ValueError(f"Invalid TCP address range: {value}")
    return [lo, hi]


def _from_description(client, table):
    desc = str(client.get("Address range", client.get("address_range", "")))
    # HMI documents its map as the FUXA ranges shifted by one thousand.
    if re.search(r"FUXA\s+address\s*\+\s*1000", desc, re.I): return None
    names = [re.escape(x) for x in LABELS[table]]
    pattern = re.compile(r"(?:^|[,;\s])(?:" + "|".join(names) + r")\s*(?:range)?\s*[:=]?\s*(\d+)\s*(?:-|\.\.|to)\s*(\d+)", re.I)
    match = pattern.search(desc)
    if match: return [int(match.group(1)), int(match.group(2))]
    # Permit one unlabelled range only for a single-table client map.
    matches = re.findall(r"(\d+)\s*(?:-|\.\.|to)\s*(\d+)", desc, re.I)
    if len(matches) == 1: return [int(matches[0][0]), int(matches[0][1])]
    return None


def client_ranges(state, client_id):
    """Return inclusive [low, high] ranges keyed by normalized Modbus table."""
    client = _client(state, client_id)
    if not client: raise ValueError(f"Unknown TCP client {client_id}")
    result = {}
    for table in TABLES:
        explicit = next((client.get(key) for key in LABELS[table] if client.get(key) not in (None, "")), None)
        if explicit is None:
            extra = {"coils":"Coils range", "discrete_inputs":"DI range", "input_registers":"IR range", "holding_registers":"HR range"}[table]
            explicit = client.get(extra)
        try: value = _pair(explicit) if explicit not in (None, "") else _from_description(client, table)
        except ValueError as exc: raise ValueError(f"Invalid {table} range for client {client_id}: {exc}") from exc
        if value is not None:
            if not BASES[table] <= value[0] <= value[1] <= 65535: raise ValueError(f"{table} range for {client_id} must be within {BASES[table]}..65535")
            result[table] = value
    if len(result) != len(TABLES):
        if re.search(r"FUXA\s+address\s*\+\s*1000", str(client.get("Address range", "")), re.I):
            fuxa = _client(state, "FUXA")
            for table in TABLES:
                base = result.get(table)
                if base is None:
                    base = client_ranges({"tcp_clients":[fuxa]}, "FUXA")[table] if fuxa else None
                    if base is None: raise ValueError(f"Cannot derive {table} range for {client_id}")
                    result[table] = [base[0] + 1000, base[1] + 1000]
        else:
            for table in TABLES:
                if table not in result: raise ValueError(f"Client {client_id} is missing a {table} address range")
    for table,(lo,hi) in result.items():
        if not BASES[table] <= lo <= hi <= 65535: raise ValueError(f"{table} range for {client_id} must be within {BASES[table]}..65535")
    # A HMI's individual explicit bounds always win; missing ones inherit the offset FUXA table.
    return result


def set_client_ranges(state, client_id, ranges):
    """Update both readable range summary and normalized per-table range columns."""
    client = _client(state, client_id)
    if not client: raise ValueError(f"Unknown TCP client {client_id}")
    if not isinstance(ranges, dict): raise ValueError("TCP client ranges must be an object")
    current = None
    normalized = {}
    for table in TABLES:
        value = ranges.get(table)
        if value is None:
            if current is None: current=client_ranges(state,client_id)
            value=current[table]
        lo, hi = _pair(value)
        normalized[table] = [lo, hi]
    fields = {"coils":"Coils range", "discrete_inputs":"DI range", "input_registers":"IR range", "holding_registers":"HR range"}
    for table, key in fields.items():
        pair = normalized[table]
        client[key] = f"{pair[0]}-{pair[1]}"
        client[table] = pair
    client["ranges"] = copy.deepcopy(normalized)
    client["Address range"] = ", ".join(f"{label} {normalized[table][0]}-{normalized[table][1]}" for table, label in (("coils","Coils"),("discrete_inputs","DI"),("input_registers","IR"),("holding_registers","HR")))
    client["address_range"] = client["Address range"]


def _table(row):
    value = str(row.get("table", row.get("sheet", ""))).casefold()
    if value in TABLES: return value
    comment = str(row.get("Comment", ""))
    match = re.search(r"Paste in\s+(COILS|DISCRETE_INPUTS|INPUT_REGISTERS|HOLDING_REGISTERS)", comment, re.I)
    if match: return match.group(1).casefold()
    raise ValueError("TCP template row has no recognized table")


def _addr_value(table, address, client_id=None):
    return int(address) + 90000 if table == "discrete_inputs" and str(client_id or "").casefold()=="fuxa" else int(address)


def client_address_for(table, address, client_id=None):
    return _addr_value(table, address, client_id)


def _address_for_table(table, base, index, client):
    return base + index


def _free_block(state, client_id, table, count, reserve=()):
    ranges = client_ranges(state, client_id)
    lo, hi = ranges[table]
    used = {int(x) for x in reserve}
    for row in state.get("tcp_points", []):
        if _table(row) != table: continue
        try: used.add(int(row.get("Modbus address", row.get("address"))))
        except (ValueError, TypeError): pass
    for start in range(lo, hi - count + 2):
        if all(address not in used for address in range(start, start + count)):
            return list(range(start, start + count))
    raise ValueError(f"No contiguous block of {count} addresses remains for client {client_id} in {table}")


def _free_fuxa_block(state, table, rows, hmi_rows):
    ranges=client_ranges(state,"FUXA"); lo,hi=ranges[table]
    hmi_lo,hmi_hi=client_ranges(state,"HMI")[table]
    used=set()
    for row in state.get("tcp_points",[]):
        if _table(row)!=table: continue
        try: used.add(int(row.get("Modbus address",row.get("address"))))
        except (TypeError,ValueError): pass
    hmi_used=set()
    for row in state.get("tcp_points",[]):
        if str(row.get("Client",row.get("client_id",""))).casefold()!="hmi" or _table(row)!=table: continue
        try: hmi_used.add(int(row.get("Modbus address",row.get("address"))))
        except (TypeError,ValueError): pass
    paired={str(r.get("Name","")) for r in hmi_rows}
    for start in range(lo,hi-len(rows)+2):
        block=list(range(start,start+len(rows)))
        if used.intersection(block): continue
        if any(str(row.get("Name","")) in paired and (not hmi_lo<=address+1000<=hmi_hi or address+1000 in hmi_used)
               for row,address in zip(rows,block)): continue
        return block
    raise ValueError(f"No contiguous FUXA {table} block remains with its paired HMI addresses free")


def free_addresses(state, client_id, table, count):
    """Compatibility entry point used by the instance editor's reviewed allocator."""
    return _free_block(state, client_id, str(table).casefold(), int(count))


def _find_templates(state, instance_id):
    instance = next((i for i in state.get("instances", []) if str(i.get("id", i.get("name"))) == str(instance_id)), None)
    if not instance: raise ValueError(f"Unknown instance {instance_id}")
    type_id = str(instance.get("type_id", ""))
    typ = next((t for t in state.get("types", []) if str(t.get("id")) == type_id or str(t.get("name")) == type_id), None)
    if not typ: raise ValueError(f"Unknown type for instance {instance_id}")
    templates = (typ.get("templates") or {}).get("TEMPLATE_TCP", [])
    out=[]
    for template in templates:
        row=copy.deepcopy(template)
        for key,value in list(row.items()):
            if isinstance(value,str): row[key]=value.replace("<INSTANCE>",str(instance_id))
        if "Instance" in row: row["Instance"]=str(instance_id)
        out.append(row)
    return out


def allocate_tcp_points(state, instance_id, client_id):
    """Allocate template points into available client address blocks; return additions."""
    if not _client(state, client_id): raise ValueError(f"Unknown TCP client {client_id}")
    templates=_find_templates(state,instance_id)
    client=str(client_id)
    if client.casefold()=="fuxa" and _client(state,"HMI"):
        selected=[r for r in templates if str(r.get("Client", "")).casefold() in {"fuxa","hmi"}]
    else:
        selected=[r for r in templates if str(r.get("Client", "")).casefold()==client.casefold()]
    if not selected: raise ValueError(f"No TCP template points exist for client {client_id}")
    selected=[r for r in selected if not any(str(p.get("Client",p.get("client_id",""))).casefold()==str(r.get("Client",client)).casefold() and str(p.get("Instance",p.get("instance_id","")))==str(instance_id) and str(p.get("Name",p.get("name","")))==str(r.get("Name","")) and str(p.get("Word", ""))==str(r.get("Word", "")) and _table(p)==_table(r) for p in state.get("tcp_points",[]))]
    if not selected: return []
    # Allocate each client/table as one contiguous block, preserving template order.
    groups={}
    for row in selected: groups.setdefault((str(row.get("Client",client)),_table(row)),[]).append(row)
    added=[]
    for (target_client,table), rows in sorted(groups.items(),key=lambda item:(0 if item[0][0].casefold()=="fuxa" else 1,item[0][1])):
        if not _client(state,target_client): continue
        addresses=[]; reserved=[]
        if target_client.casefold()=="hmi":
            for row in rows:
                match=next((p for p in state.get("tcp_points",[]) if str(p.get("Client",p.get("client_id",""))).casefold()=="fuxa" and _table(p)==table and str(p.get("Instance",p.get("instance_id","")))==str(instance_id) and str(p.get("Name",p.get("name","")))==str(row.get("Name","")) and str(p.get("Word",""))==str(row.get("Word",""))),None)
                candidate=int(match.get("Modbus address",match.get("address")))+1000 if match else None
                lo,hi=client_ranges(state,target_client)[table]
                occupied={int(p.get("Modbus address",p.get("address"))) for p in state.get("tcp_points",[]) if _table(p)==table and str(p.get("Modbus address",p.get("address",""))).isdigit()}
                if candidate is not None:
                    if not lo<=candidate<=hi: raise ValueError(f"HMI address {candidate} for {row.get('Name')} is outside configured range {lo}-{hi}")
                    if candidate in occupied or candidate in reserved: raise ValueError(f"Paired HMI address {candidate} for {row.get('Name')} is already occupied")
                    addresses.append(candidate); reserved.append(candidate)
                else: addresses.append(None)
        else: addresses=[None]*len(rows)
        if any(a is None for a in addresses):
            if target_client.casefold()=="fuxa" and _client(state,"HMI"):
                paired_rows=[r for r in templates if str(r.get("Client","")).casefold()=="hmi" and _table(r)==table]
                free=_free_fuxa_block(state,table,rows,paired_rows)
            else:
                free=_free_block(state,target_client,table,len(rows),reserve=reserved)
            addresses=[a if a is not None else free[i] for i,a in enumerate(addresses)]
        for row,address in zip(rows,addresses):
            row["Client"]=target_client
            row["Modbus address"]=address
            row["Client address"]=_addr_value(table,address,target_client)
            try:
                from backend.v2_instance_editor import _tcp_variable
                row["GVL_MODBUS variable"]=_tcp_variable(table,address)
            except (ImportError, ValueError): pass
            sheet=table.upper()
            row.update(table=table,sheet=sheet,id=f"{table}:{target_client}:{address}",address=address,client_id=target_client,
                       instance_id=str(instance_id),member_name=_member_from_com(row.get("GVL_COM variable")))
            state.setdefault("tcp_points",[]).append(row); added.append(row)
    return added


def _member_from_com(value):
    from backend.v2_engine import _member_from_var
    return _member_from_var(value)
