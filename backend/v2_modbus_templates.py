"""Type-level Modbus mappings and their application to project instances."""
from collections import defaultdict
import re

from pydantic import BaseModel, Field

from backend.v2_addressing import _table, client_ranges, client_address_for
from backend.v2_fb_flow import variable_options
from backend.v2_manual_comm import DOUBLE_WORD_TYPES, WORD_TYPES, WORD_ORDERS


TABLES = {"coils": "Coil", "discrete_inputs": "Discrete input",
          "input_registers": "IR", "holding_registers": "HR"}
BITS = {"coils", "discrete_inputs"}


class Mapping(BaseModel):
    protocol: str
    table: str
    member: str
    inbound: bool
    register_address: int | None = Field(default=None, ge=0, le=65535, alias="register")
    order: str = "LSW_FIRST"
    bank: str | None = None
    bank_type: str | None = None
    function: str | None = None
    channel: str | None = None


def _address(value):
    raw = str(value)
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw)


def _members(typ, state):
    options = variable_options(typ, state)
    members = {row["path"][4:]: str(row["iec_type"]).upper() for row in options
               if row["path"].startswith("COM.") and not row["path"].endswith(".SPARE")}
    members.update({f"{path}.{bit}": "BOOL" for path, dtype in list(members.items())
                    if dtype == "BYTE" and path.endswith("]") for bit in range(8)})
    return members


def validate_rows(state, typ, rows):
    members = _members(typ, state)
    signals, occupied = set(), set()
    for row in rows:
        dtype = members.get(row.member)
        if dtype not in WORD_TYPES | DOUBLE_WORD_TYPES | {"BOOL"}:
            raise ValueError(f"Choisis une variable COM compatible : {row.member or 'variable vide'}.")
        if row.protocol not in {"tcp", "rtu"} or row.table not in TABLES or (dtype == "BOOL") != (row.table in BITS):
            raise ValueError(f"Table Modbus incompatible avec {row.member} ({dtype}).")
        if row.table in {"discrete_inputs", "input_registers"} and row.inbound != (row.protocol == "rtu"):
            raise ValueError(f"{row.member} : cette table est en lecture seule côté client.")
        if row.order not in WORD_ORDERS:
            raise ValueError(f"Ordre des mots invalide pour {row.member}.")
        key = (row.protocol, row.table, row.member, row.inbound)
        if key in signals:
            raise ValueError(f"{row.member} apparaît deux fois dans le même mappage.")
        signals.add(key)
        width = 2 if dtype in DOUBLE_WORD_TYPES else 1
        if row.bank or row.bank_type:
            if row.protocol != "rtu" or not row.bank or not row.bank_type or not re.fullmatch(r"[A-Za-z_]\w*\[\d+\](?:\.\d+)?", row.bank):
                raise ValueError(f"Adresse de variable PLC invalide pour {row.member}.")
            bank = re.fullmatch(r"ARRAY\[0\.\.(\d+)\] OF (WORD|BOOL|BYTE)", row.bank_type)
            cell = re.search(r"\[(\d+)\](?:\.(\d+))?$", row.bank)
            if not bank or int(cell[1]) + width - 1 > int(bank[1]) or (
                (bank[2] == "BYTE" and (cell[2] is None or int(cell[2]) > 7 or dtype != "BOOL")) or
                (bank[2] != "BYTE" and (cell[2] is not None or (bank[2] == "BOOL") != (dtype == "BOOL")))
            ):
                raise ValueError(f"Type de tableau PLC invalide pour {row.member}.")
        if row.protocol == "rtu":
            if row.register_address is None or row.register_address + width > 65536:
                raise ValueError(f"Indique le registre RTU du fabricant pour {row.member}.")
            slots = {(row.table, number) for number in range(row.register_address, row.register_address + width)}
            if slots & occupied:
                raise ValueError(f"Registre RTU déjà utilisé dans le modèle : {row.register_address}.")
            occupied.update(slots)
    return rows


def _from_points(typ, state, protocol, points):
    from backend.v2_plc import _source, _source_objects, NS
    from backend.v2_codesys import _type_text

    members = _members(typ, state)
    register_types = {}
    if protocol == "rtu":
        gvl = _source_objects(_source(state)).get("GVL_MODBUS")
        if gvl is not None:
            register_types = {node.get("name"): _type_text(node.find(NS + "type/*")) for node in gvl.findall(NS + "variable")}
    rows = {}
    for point in points:
        path = str(point.get("GVL_COM variable") or "").split(".", 2)
        if len(path) != 3:
            continue
        member = re.sub(r"\.Word_value\[[01]\]$", ".Real_value", path[-1])
        dtype = members.get(member)
        if dtype not in WORD_TYPES | DOUBLE_WORD_TYPES | {"BOOL"}:
            continue
        inbound = str(point.get("Direction") or point.get("direction")).casefold() in {"client to plc", "equipment to plc"}
        table = _table(point) if protocol == "tcp" else next((k for k, v in TABLES.items() if v == point.get("Table")), None)
        if table is None:
            continue
        raw = point.get("Equipment address") if protocol == "rtu" else point.get("Modbus address")
        number = _address(raw) if raw not in (None, "") else None
        bank = bank_type = None
        if protocol == "rtu":
            mapped = str(point.get("GVL_MODBUS variable") or "").removeprefix("GVL_MODBUS.")
            base, bracket, index = mapped.partition("[")
            if bracket:
                reference = next((key for key in register_types if key.startswith(typ["name"] + "_") and
                                  (base == key or base.endswith("_" + key[len(typ["name"]) + 1:]))), None)
                if reference and reference.startswith(typ["name"] + "_"):
                    bank = reference[len(typ["name"]) + 1:] + bracket + index
                    bank_type = register_types[reference]
        key = (table, member, inbound)
        order = "MSW_FIRST" if point.get("Word") == "MSW" else "LSW_FIRST"
        if key not in rows or (number is not None and number < rows[key][0]):
            rows[key] = (number if number is not None else 65536, Mapping(
                protocol=protocol, table=table, member=member, inbound=inbound, register=number if protocol == "rtu" else None,
                order=order, bank=bank, bank_type=bank_type, function=point.get("Function"), channel=point.get("CODESYS channel")))
    return [row for _, row in rows.values()]


def seed_rows(state, typ):
    templates = typ.get("templates") or {}
    names = {row.get("name") or row.get("id") for row in state.get("instances", [])
             if row.get("type_id") in (typ.get("id"), typ.get("name"))}
    result = []
    for protocol, collection in (("tcp", "tcp_points"), ("rtu", "rtu_channels")):
        points = templates.get("TEMPLATE_" + protocol.upper()) or []
        if protocol == "tcp" and any(row.get("Client") == "HMI" for row in points):
            points = [row for row in points if row.get("Client") == "HMI"]
        if not points:
            by_instance = defaultdict(list)
            for row in state.get(collection, []):
                if (name := row.get("Instance") or row.get("instance_id")) in names:
                    by_instance[(name, row.get("Client") if protocol == "tcp" else None)].append(row)
            points = max(by_instance.values(), key=len, default=[])
        result.extend(_from_points(typ, state, protocol, points))
    return validate_rows(state, typ, result)


def rows_for(state, typ):
    if "modbus_template" in typ:
        return [Mapping.model_validate(row) for row in typ["modbus_template"]]
    return seed_rows(state, typ)


def apply_template(state, instance_id, protocol, client=None, line=None, address=None, include=False):
    from backend.v2_capacity import tcp_capacity
    from backend.v2_configuration import apply_changes
    from backend.v2_instance_editor import _tcp_variable
    from backend.v2_program_membership import freeze_membership

    inst = next((i for i in state["instances"] if instance_id in (i.get("id"), i.get("name"))), None)
    typ = next((t for t in state["types"] if inst and inst["type_id"] in (t["id"], t["name"])), None)
    if not typ or protocol not in {"tcp", "rtu"}:
        raise ValueError("Choisis une instance et un protocole Modbus.")
    name = inst.get("name") or inst["id"]
    rows = validate_rows(state, typ, [row for row in rows_for(state, typ) if row.protocol == protocol])
    if not rows:
        raise ValueError("Aucun modèle pour ce protocole. Configure-le dans Types.")
    tcp = protocol == "tcp"
    collection = "tcp_points" if tcp else "rtu_channels"
    points = state.setdefault(collection, [])
    if tcp:
        ranges, capacity = client_ranges(state, client), tcp_capacity(state)
        summary = []
    else:
        slave = next((r for r in state.get("rtu_slaves", []) if r.get("Instance") == name), None)
        if slave is None:
            if line not in {r.get("Line") or r.get("id") for r in state.get("rtu_lines", [])}:
                raise ValueError("Choisis une ligne RS-485.")
            maximum = 31 if typ["name"] == "DIP1500" else 247
            if type(address) is not int or not 1 <= address <= maximum:
                raise ValueError(f"Choisis une adresse esclave entre 1 et {maximum}.")
            if any(r.get("Line") == line and str(r.get("Slave address")).lstrip("0") == str(address) for r in state.get("rtu_slaves", [])):
                raise ValueError(f"L’adresse esclave {address} est déjà utilisée sur {line}.")
            default = typ.get("rtu_default", {}).get("32-bit word order")
            apply_changes(state, [{"op": "create", "collection": "rtu_slaves", "values": {
                "Instance": name, "Line": line, "Slave address": address, "CODESYS slave device": name,
                "32-bit word order": default or "Voir le modèle Modbus",
            }}])
            summary = [f"Nouvel esclave {name} · {line} · adresse {address}"]
        else:
            summary = [f"Esclave {name} · {slave.get('Line')} · adresse {slave.get('Slave address')}"]
    added = []
    members = _members(typ, state)
    for row in rows:
        table = row.table if tcp else TABLES[row.table]
        direction = ("Client to PLC" if row.inbound else "PLC to Client") if tcp else ("Equipment to PLC" if row.inbound else "PLC to Equipment")
        dtype = members[row.member]
        wide = dtype in DOUBLE_WORD_TYPES
        width = 2 if wide else 1
        union = row.member.endswith(".Real_value") and members.get(row.member.removesuffix(".Real_value") + ".Word_value[0]") == "WORD"
        root = "GVL_COM.COM_" + name + "."
        member = root + row.member
        union_base = member.removesuffix(".Real_value")
        matching = [p for p in points if (p.get("Instance") or p.get("instance_id")) == name and
                    str(p.get("table") or p.get("Table")).casefold() == table.casefold() and
                    (not tcp or str(p.get("Client") or p.get("client_id")).casefold() == str(client).casefold()) and
                    (p.get("GVL_COM variable") == member or union and
                     re.fullmatch(re.escape(union_base) + r"\.Word_value\[[01]\]", str(p.get("GVL_COM variable"))))]
        if matching:
            if len(matching) != width or wide and {p.get("Word") for p in matching} != {"LSW", "MSW"}:
                raise ValueError(f"{row.member} possède un mappage incomplet. Corrige-le avant d’appliquer le modèle.")
            if any(str(p.get("Direction") or p.get("direction")).casefold() != direction.casefold() for p in matching):
                raise ValueError(f"{row.member} est déjà mappé dans l’autre sens.")
            field = "Modbus address" if tcp else "Equipment address"
            addresses = sorted(_address(p.get(field, p.get("address"))) for p in matching)
            if wide and addresses[1] != addresses[0] + 1:
                raise ValueError(f"{row.member} utilise deux adresses non consécutives.")
            summary.append(f"{row.member} · {table} · " + " + ".join(map(str, addresses)) + " (existant)")
            continue
        field = "Modbus address" if tcp else "Equipment address"
        used = {_address(p.get(field, p.get("address"))) for p in points
                if str(p.get("table") or p.get("Table")).casefold() == table.casefold()
                and (tcp or (p.get("Instance") or p.get("instance_id")) == name)
                and p.get(field, p.get("address")) not in (None, "", "TO CHECK")}
        if tcp:
            lo, hi = ranges[table]
            cap = capacity.get(table, [lo, hi])
            start = next((n for n in range(max(lo, cap[0]), min(hi, cap[1]) - width + 2)
                          if all(n + i not in used for i in range(width))), None)
        else:
            start = row.register_address if all(row.register_address + i not in used for i in range(width)) else None
        if start is None:
            raise ValueError(f"Aucune adresse libre pour {row.member} dans {table} ({width} registre(s)).")
        for offset, word in enumerate(WORD_ORDERS[row.order] if wide else (None,)):
            number = start + offset
            path = f"{union_base}.Word_value[{0 if word == 'LSW' else 1}]" if union else member
            point = {"id": f"{protocol}:{client or name}:{table}:{number}", "Instance": name, "instance": name,
                     "instance_id": name, "Name": f"{name}.{row.member}", "Direction": direction,
                     "direction": direction, "Type": dtype, "data_type": dtype, "table": table,
                     "member_name": row.member.split(".")[0].split("[")[0], "Word": word, "word": word,
                     "address": number, field: number, "GVL_COM variable": path}
            if tcp:
                point.update(Client=client, client_id=client, sheet=table.upper(),
                             **{"Client address": client_address_for(table, number, client),
                                "GVL_MODBUS variable": _tcp_variable(table, number)})
            else:
                mapped = f"{name}_{row.bank}" if row.bank else f"RTU_{name}_{table.replace(' ', '_')}_{number}"
                if row.bank and wide:
                    mapped = re.sub(r"\[(\d+)\]$", lambda match: f"[{int(match[1]) + offset}]", mapped)
                function = row.function or ({
                    "coils": "FC01 Read Coils", "discrete_inputs": "FC02 Read Discrete Inputs",
                    "input_registers": "FC04 Read Input Registers", "holding_registers": "FC03 Read Holding Registers",
                }[row.table] if row.inbound else "FC15 Write Multiple Coils" if row.table == "coils" else "FC16 Write Multiple Registers")
                point.update(Table=table, sheet=name, slave_id=name, manual_scalar=not row.bank,
                             mapped_bank_type=row.bank_type, mapped_register_type="WORD" if union else None,
                             dip1500_register=bool(row.bank and typ["name"] == "DIP1500"), Function=function,
                             **{"Channel offset": offset, "CODESYS channel": row.channel,
                                "GVL_MODBUS variable": "GVL_MODBUS." + mapped})
            points.append(point)
            added.append(point)
        summary.append(f"{row.member} · {table} · " + " + ".join(str(start + i) for i in range(width)))
    if include:
        membership = freeze_membership(state)
        for block in ("GFC_COM_IN", "GFC_COM_OUT"):
            if name not in membership[block]:
                membership[block].append(name)
    return added, summary


def seed_project(session, name, expected_revision=None):
    from sqlalchemy import func, select
    from backend import v2_store

    project = session.scalar(select(v2_store.V2Project).where(func.lower(v2_store.V2Project.name) == name.casefold()))
    if project is None:
        raise ValueError(f"Projet introuvable : {name}.")
    session.info["workspace_id"] = project.id
    state = v2_store.load_project(session)
    revision = state["revision"]
    if expected_revision is not None and expected_revision != revision:
        raise ValueError(f"La révision a changé : {revision}.")
    counts = {}
    for typ in state["types"]:
        if "modbus_template" in typ:
            continue
        rows = seed_rows(state, typ)
        typ["modbus_template"] = [row.model_dump(by_alias=True) for row in rows]
        counts[typ["name"]] = {protocol: sum(row.protocol == protocol for row in rows) for protocol in ("tcp", "rtu")}
    result = {"revision": revision, "types": counts}
    if counts and expected_revision is not None:
        session.info["version_event"] = {"kind": "modbus_templates", "comment": "Modèles Modbus TCP et RTU du catalogue"}
        saved = v2_store.save_project(session, state, username="system:modbus-templates", expected_revision=revision)
        result["saved_revision"] = saved["revision"]
    return result


if __name__ == "__main__":
    import argparse
    import json
    from sqlalchemy.orm import Session
    from backend.db import make_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--apply-revision", type=int)
    args = parser.parse_args()
    with Session(make_engine()) as session:
        result = seed_project(session, args.project, args.apply_revision)
        if args.apply_revision is not None:
            session.commit()
        print(json.dumps(result, ensure_ascii=False))
