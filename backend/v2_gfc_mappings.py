"""Scoped choices for the generated GFC copy review."""
from __future__ import annotations

import re
from collections import defaultdict

from backend.v2_catalog import IEC_TYPES
from backend.v2_fb_flow import variable_options
from backend.v2_manual_comm import pair_key

COPY_BLOCKS = {"GFC_COM_IN", "GFC_COM_OUT", "GFC_IO_IN", "GFC_IO_OUT", "GFC_EXC"}
COPY_COLLECTIONS = ("tcp_points", "rtu_channels", "io_channels", "virtual_io", "exchange_calls")


def modbus_inbound(row):
    return str(row.get("Direction") or row.get("direction") or "").strip().casefold() in {
        "client to plc", "equipment to plc",
    }


def instance_name(row):
    return str(row.get("Instance") or row.get("instance_id") or row.get("instance") or "")


def connection_key(collection, row):
    if collection in {"tcp_points", "rtu_channels"} and (pair := pair_key(row)):
        return collection + ":pair:" + "|".join(pair)
    return f"{collection}:{row.get('id', '')}"


def _connection_targets(state):
    slots = defaultdict(set)
    for collection in COPY_COLLECTIONS:
        for row in state.get(collection, []):
            if collection == "exchange_calls":
                target = ("GFC_EXC", row.get("instance_a_id"), row.get("instance_b_id"),
                          row.get("function_id") or row.get("FC"), row.get("parameter_b_value"))
            elif collection in {"io_channels", "virtual_io"}:
                target = (str(row.get("I/O type") or "").upper(), instance_name(row), row.get("GVL_IO member"))
                if not row.get("GVL_IO member") or not row.get("GVL_IOMAP variable"):
                    continue
            else:
                target = (modbus_inbound(row), instance_name(row), row.get("GVL_COM variable"))
            slots[connection_key(collection, row)].add(target)
    return slots


def reconcile_copy_choices(previous, state):
    """Keep reviewed corrections attached to the mapping they were made against."""
    overrides = state.get("gfc_copy_overrides")
    if not previous or not overrides or not any(previous.get(key) != state.get(key) for key in COPY_COLLECTIONS):
        return
    before, after = _connection_targets(previous), _connection_targets(state)
    for choices in overrides.values():
        for key in list(choices):
            if key not in after or before.get(key) != after[key]:
                del choices[key]


def manual_mapping_changes(previous, state, blocks=COPY_BLOCKS):
    """Identify saved manual bodies that mask changes to generated copies."""
    from backend.v2_codesys import _st
    from backend.v2_plc import build_program

    overrides = state.get("st_overrides") or {}
    manual = {block for block in blocks if overrides.get(block, {}).get("body") is not None}
    if not manual or not any(previous.get(key) != state.get(key) for key in COPY_COLLECTIONS):
        return []

    def automatic(project):
        from copy import deepcopy

        project = deepcopy(project)
        for block in manual:
            project.get("st_overrides", {}).get(block, {}).pop("body", None)
        return build_program(project)

    before, after = automatic(previous), automatic(state)
    return [
        f"{block} utilise un corps ST manuel. Pour appliquer ces recopies, adapte ce corps ou rétablis le ST généré."
        for block in sorted(manual) if block in before and block in after
        and _st(before[block]["element"]) != _st(after[block]["element"])
    ]


def is_dip1500(state, row):
    name = instance_name(row)
    identifiers = {str(key) for typ in state.get("types", []) if str(typ.get("name")).casefold() == "dip1500"
                   for key in (typ.get("id"), typ.get("name"))}
    return any(str(inst.get("name") or inst.get("id")) == name and str(inst.get("type_id")) in identifiers
               for inst in state.get("instances", []))


def _member_direction(path):
    leaf = path.split(".", 2)[-1].split(".", 1)[0]
    match = re.search(r"(?:^|_)(INPUT|OUTPUT)_", leaf, re.I)
    return match[1].lower() if match else None


def editable_rtu_banks(state, rows):
    """Identify complete one-to-one banks that can be split without changing copies."""
    types = {str(key): typ for typ in state.get("types", []) for key in (typ.get("id"), typ.get("name")) if key}
    instances = {str(item.get("name") or item.get("id")): item for item in state.get("instances", [])}
    banks = defaultdict(list)
    for row in rows:
        register = re.fullmatch(r"(.+)\[(-?\d+)\]", str(row.get("GVL_MODBUS variable") or ""))
        member = re.fullmatch(r"GVL_COM\.COM_[^.]+\.([A-Za-z_]\w*)\[(-?\d+)\]", str(row.get("GVL_COM variable") or ""))
        if register and member and register[2] == member[2] and not row.get("manual_scalar"):
            banks[(instance_name(row), register[1], member[1], row.get("Direction"))].append((int(member[2]), row))
    eligible = []
    for (name, _, member, _), bank in banks.items():
        typ = types.get(str(instances.get(name, {}).get("type_id")), {})
        dut = next((item for item in typ.get("duts", []) if item.get("role") == "COM"), {})
        field = next((item for item in dut.get("members", []) if item.get("name") == member), {})
        bounds = re.fullmatch(r"ARRAY\s*\[\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\]\s*OF\s+(\w+)",
                              str(field.get("iec_type") or ""), re.I)
        if not bounds or bounds[3].upper() not in IEC_TYPES:
            continue
        low, high = int(bounds[1]), int(bounds[2])
        if len(bank) == high - low + 1 and all(index == low + offset for offset, index in
                                               enumerate(sorted(index for index, _ in bank))):
            eligible.append({connection_key("rtu_channels", row) for _, row in bank})
    return eligible


def review(state, block, included):
    if block not in COPY_BLOCKS:
        return []
    from backend.v2_plc import _tcp_address_expression
    from backend.v2_io_copies import variable_direction

    inbound = block.endswith("_IN")
    role = "COM" if "_COM_" in block else "IO" if "_IO_" in block else "EXC"
    types = {str(key): typ for typ in state.get("types", []) for key in (typ.get("id"), typ.get("name")) if key}
    instances = {str(item.get("name") or item.get("id")): item for item in state.get("instances", [])}
    members = {}
    groups = {}
    for name in included:
        item = instances.get(name)
        if not item:
            continue
        options = variable_options(types.get(str(item.get("type_id")), {}), state)
        paths = {f"GVL_{row['path'].split('.')[0]}.{row['path'].split('.')[0]}_{name}.{row['path'].split('.', 1)[1]}": str(row["iec_type"]).upper()
                 for row in options if row["path"].split('.')[0] in ({'COM', 'HMI'} if role == 'COM' else {role}) and str(row["iec_type"]).upper() in IEC_TYPES
                 and not row["path"].endswith(".SPARE")}
        members[name] = paths
        groups[name] = {"instance": name, "members": [{"path": path, "type": kind} for path, kind in paths.items()], "rows": []}
    previous = (state.get("gfc_copy_overrides") or {}).get(block, {})
    entries = {}

    def add(collection, row, source, target):
        name = instance_name(row)
        if not source or not target or name not in members:
            return
        key = connection_key(collection, row)
        if key in entries:
            entries[key]["endpoints"].append(source)
            return
        kind = members[name].get(target)
        if not kind and role == "COM" and target.rsplit(".", 1)[-1].isdigit():
            kind = "BOOL"
        direction = "input" if inbound else "output"
        choices = [path for path, candidate in members[name].items() if path.split('.')[0] == target.split('.')[0] and kind == candidate and
                   _member_direction(path) in (None, direction)]
        if target not in choices:
            choices.insert(0, target)
        selected = previous.get(key)
        entries[key] = {"key": key, "endpoints": [source], "automatic": target,
                        "member": selected if selected in choices else target, "choices": choices,
                        "type": kind or str(row.get("Type") or "")}
        groups[name]["rows"].append(entries[key])

    if role == "COM":
        for row in state.get("tcp_points", []):
            if modbus_inbound(row) == inbound:
                register = _tcp_address_expression(row, state)
                if register:
                    add("tcp_points", row, "GVL_MODBUS." + register, row.get("GVL_COM variable"))
        rtu = state.get("rtu_channels", [])
        for row in rtu:
            if modbus_inbound(row) == inbound:
                register, target = row.get("GVL_MODBUS variable"), row.get("GVL_COM variable")
                add("rtu_channels", row, register, target)
    elif role == "IO":
        for collection in ("io_channels", "virtual_io"):
            for row in state.get(collection, []):
                if variable_direction(row) == ("input" if inbound else "output"):
                    add(collection, row, row.get("GVL_IOMAP variable"), row.get("GVL_IO member"))
    else:
        for row in state.get("exchange_calls", []):
            first = str(row.get("instance_a_id") or row.get("Instance A") or "")
            second = str(row.get("instance_b_id") or row.get("Instance B") or "")
            if first not in groups or second not in groups:
                continue
            key = connection_key("exchange_calls", row)
            target = str(row.get("parameter_b_value") or "")
            options = [f"GVL_EXC.EXC_{name}" for name in groups if name != first and
                       instances[name].get("type_id") == instances[second].get("type_id")]
            if target not in options:
                options.insert(0, target)
            selected = previous.get(key)
            groups[first]["rows"].append({"key": key, "endpoints": [str(row.get("function_id") or row.get("FC") or "")],
                                          "automatic": target, "member": selected if selected in options else target,
                                          "choices": options, "type": "EXC"})
    return list(groups.values())


def resolved_targets(state, block, included):
    if not (state.get("gfc_copy_overrides") or {}).get(block):
        return {}
    return {row["key"]: row["member"] for group in review(state, block, included) for row in group["rows"]
            if row["member"] != row["automatic"]}


def save_choices(state, block, included, changes):
    if block not in COPY_BLOCKS:
        raise ValueError("Ce bloc ne prend pas en charge la correction des recopies.")
    groups = review(state, block, included)
    available = {row["key"]: row for group in groups for row in group["rows"]}
    overrides = state.setdefault("gfc_copy_overrides", {}).setdefault(block, {})
    for key, target in changes.items():
        row = available.get(key)
        if row is None or target not in row["choices"]:
            raise ValueError("Choisis une variable compatible appartenant aux instances incluses.")
        if target == row["automatic"]:
            overrides.pop(key, None)
        else:
            overrides[key] = target
    if block.endswith("_IN"):
        previous = defaultdict(set)
        proposed = defaultdict(set)
        for key, row in available.items():
            previous[row["member"]].add(key)
            proposed[overrides.get(key, row["automatic"])].add(key)
        if any(len(keys) > 1 and not keys <= previous[path] for path, keys in proposed.items()):
            raise ValueError("Deux sources écriraient dans la même variable d’instance.")
