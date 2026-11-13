"""Normalize the saved DIP1500 FB without replacing any project data."""
from __future__ import annotations

import copy
from functools import lru_cache
import json
from pathlib import Path
import re

from backend.v2_fb_flow import ASSIGN, ensure_flow, flow_view, render_flow, update_flow, variable_options
from backend.v2_plc_validation import _st_code


LEGACY_BANKS = {"COM_INPUT_IR", "COM_INPUT_HR_COUNTERS", "COM_OUTPUT_COILS",
                "COM_OUTPUT_HR_CMD", "COM_OUTPUT_HR_CONFIG"}


@lru_cache(maxsize=1)
def _reference():
    return json.loads(Path(__file__).with_name("dip1500_simplified.json").read_text(encoding="utf-8"))


def _point(row):
    path = str(row.get("GVL_MODBUS variable") or "").removeprefix("GVL_MODBUS.")
    point = _reference()["rtu"].get(path)
    if point and _register_address(row.get("Equipment address")) == int(point["address"], 16):
        return point
    return None


def _register_address(value):
    raw = str(value)
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw)


def _signal(path):
    return re.sub(r"(\.Word_value)\[\d+\]$", r"\1", path, flags=re.I).casefold()


def is_simplified(typ):
    return typ.get("dip1500_profile") == "named-registers-v1" or any(
        member["name"] == "COM_INPUT_RUN_ACTUAL"
        for dut in typ.get("duts", []) if dut.get("role") == "COM" for member in dut.get("members", [])
    )


def is_register(row):
    return row.get("dip1500_register") or bool(re.fullmatch(
        r"GVL_MODBUS\.\w+_(?:Coils|InputRegisters|Holding_Counters|Holding_Cmd|Holding_Config)\[\d+\]",
        str(row.get("GVL_MODBUS variable") or ""), re.I,
    ) and re.fullmatch(
        r"GVL_COM\.COM_\w+\.COM_(?:INPUT|OUTPUT)_\w+(?:\.Word_value\[[01]\])?",
        str(row.get("GVL_COM variable") or ""), re.I,
    ))


def _update_channel(row):
    point = _point(row)
    if point:
        member = point["path"]
        prefix = row["GVL_COM variable"].split(".", 2)[0:2]
        row.update({"GVL_COM variable": ".".join([*prefix, member]),
                    "member_name": member.split(".")[0], "dip1500_register": True})
        return True
    return False


def _sync_modbus(state):
    from backend.v2_configuration import synchronize_modbus_workbook

    for name in ("MODBUS_TCP.xlsx", "MODBUS_RTU.xlsx"):
        if book := state.get("workbooks", {}).get(name):
            synchronize_modbus_workbook(state, book)


def simplify(state):
    """Install the reviewed DIP1500 logic and rewrite its existing RTU targets."""
    from backend.v2_engine import _sheet_map
    from backend.v2_fb_flow import convert_body
    from backend.v2_hmi import load_hmi_pack
    from backend.v2_plc_validation import _st_for_compare
    from backend.v2_type_creation import _sync_type_dut_sheet

    typ = next((row for row in state.get("types", []) if row.get("name") == "DIP1500"), None)
    if not typ or is_simplified(typ):
        return False
    fb = typ["fb"]
    original = copy.deepcopy(load_hmi_pack())
    original_fb = next(row["fb"] for row in original["types"] if row["name"] == "DIP1500")
    baseline = {_st_for_compare(original_fb["body"])}
    regularize(original)
    baseline.add(_st_for_compare(next(row["fb"]["body"] for row in original["types"] if row["name"] == "DIP1500")))
    overrides = state.get("st_overrides") or {}
    if (_st_for_compare(fb.get("body") or "") not in baseline or
            any(overrides.get(name) for name in (fb["name"], "DUT_COM_DIP1500", "DUT_INT_DIP1500"))):
        raise ValueError("Le DIP1500 a du ST personnalisé dans les objets à remplacer. La fusion doit être revue.")

    reference = _reference()
    original_type = next(row for row in original["types"] if row["name"] == "DIP1500")
    for dut in typ["duts"]:
        role = dut["role"]
        if role not in reference["members"]:
            continue
        previous = {member["name"]: member for member in dut["members"]}
        original_names = {m["name"] for d in original_type["duts"] if d["role"] == role for m in d["members"]}
        removed = LEGACY_BANKS if role == "COM" else original_names - {m["name"] for m in reference["members"][role]}
        members = []
        for source in reference["members"][role]:
            name = source["name"]
            entry = dict(previous.get(name, {}))
            if entry.get("iec_type") not in (None, source["iec_type"]):
                raise ValueError(f"Type personnalisé incompatible : {dut['name']}.{name}.")
            entry.update(source)
            entry.setdefault("id", f"DIP1500:{role}:named:{name}")
            if role == "COM":
                entry["section"] = "Modbus RTU · Input" if name.startswith("COM_INPUT_") else "Modbus RTU · Output"
            members.append(entry)
        known = {m["name"] for m in members} | removed
        dut["members"] = members + [member for name, member in previous.items() if name not in known]
        for index, member in enumerate(dut["members"]):
            member["position"] = index
        _sync_type_dut_sheet(state, typ, role)

    flow = convert_body(reference["body"])
    fb.update(body=render_flow(flow), flow=flow)
    names = {str(row.get("name") or row.get("id")) for row in state.get("instances", [])
             if row.get("type_id") in {typ["id"], typ["name"]}}
    templates = typ.get("templates", {}).get("TEMPLATE_RTU", [])
    if len(templates) != len(reference["rtu"]) or not all(_update_channel(row) for row in templates):
        raise ValueError("Le modèle RTU DIP1500 ne correspond pas aux 26 points du XML.")
    for row in state.get("rtu_channels", []):
        if (row.get("Instance") or row.get("instance_id")) in names:
            if not _update_channel(row) and re.split(r"[.\[]", row["GVL_COM variable"].split(".", 2)[-1])[0] in LEGACY_BANKS:
                raise ValueError(f"Registre DIP1500 personnalisé à vérifier : {row['GVL_MODBUS variable']}.")
    targets = {(row["Table"], _register_address(row["Equipment address"])): row["GVL_COM variable"].split(".", 2)[-1]
               for row in templates}
    for sheet, table in (("RTU_COILS", "Coil"), ("RTU_IR", "IR"), ("RTU_HR", "HR")):
        for row in typ.get("rtu", {}).get(sheet, []):
            key = (table, _register_address(row["Equipment address"]))
            if key in targets:
                row["DUT_COM member"] = targets[key]
    book = state["workbooks"][typ["source_file"]]
    for sheet in ("TEMPLATE_RTU", "RTU_COILS", "RTU_IR", "RTU_HR"):
        if dest := _sheet_map(book).get(sheet):
            rows = templates if sheet == "TEMPLATE_RTU" else typ.get("rtu", {}).get(sheet, [])
            dest["rows"] = [[row.get(header) for header in dest["headers"]] for row in rows]
    typ["dip1500_profile"] = "named-registers-v1"
    _sync_modbus(state)
    return True


def configure_instance(state, instance_id, mode, *, client_id="HMI", line_id=None, address=None, include=False):
    from backend.v2_addressing import allocate_tcp_points
    from backend.v2_configuration import apply_changes, _capacity_warnings
    from backend.v2_program_membership import freeze_membership

    instance = next((row for row in state.get("instances", []) if instance_id in (row.get("name"), row.get("id"))), None)
    typ = next((row for row in state.get("types", []) if instance and instance["type_id"] in (row["name"], row["id"])), None)
    if not typ or typ["name"] != "DIP1500" or not is_simplified(typ):
        raise ValueError("Sélectionne une instance du type DIP1500 simplifié.")
    name = instance.get("name") or instance["id"]
    if mode == "tcp":
        from backend.v2_configuration import validate_configuration

        baseline = set(_capacity_warnings(state)) | set(validate_configuration(state)["errors"])
        added = allocate_tcp_points(state, name, client_id)
        errors = (set(_capacity_warnings(state)) | set(validate_configuration(state)["errors"])) - baseline
        if errors:
            raise ValueError(sorted(errors)[0])
    elif mode == "rtu":
        slave = next((row for row in state.get("rtu_slaves", []) if row.get("Instance") == name), None)
        if slave is None:
            if not any(line_id in (line.get("Line"), line.get("id")) for line in state.get("rtu_lines", [])):
                raise ValueError("Choisis la ligne RS-485 de la pompe.")
            if not isinstance(address, int) or isinstance(address, bool) or not 1 <= address <= 31:
                raise ValueError("Choisis une adresse esclave DIP1500 entre 1 et 31.")
            if any(str(row.get("Line") or row.get("line_id")) == line_id
                   and str(row.get("Slave address", row.get("address"))).lstrip("0") == str(address)
                   for row in state.get("rtu_slaves", [])):
                raise ValueError(f"L’adresse {address} est déjà utilisée sur {line_id}.")
            apply_changes(state, [{"collection": "rtu_slaves", "op": "create", "values": {
                "Instance": name, "Line": line_id, "Slave address": address,
                "CODESYS slave device": name, "32-bit word order": "CDAB (LSW first)",
            }}])
        existing = [row for row in state.get("rtu_channels", []) if row.get("Instance") == name]
        by_address = {(row["Table"], _register_address(row["Equipment address"])): row for row in existing}
        added = []
        for template in typ["templates"]["TEMPLATE_RTU"]:
            table, register = template["Table"], template["Equipment address"]
            path = template["GVL_COM variable"].replace("<INSTANCE>", name)
            if previous := by_address.get((table, _register_address(register))):
                if _signal(previous["GVL_COM variable"]) != _signal(path):
                    raise ValueError(f"{name} : le registre {table} {register} est déjà relié à une autre variable.")
                continue
            bank = template["GVL_MODBUS variable"].removeprefix("GVL_MODBUS.DIP1500_")
            row = dict(template, id=f"{name}:{table}:{register}", Instance=name, instance=name,
                       instance_id=name, slave_id=name, table=table, address=register,
                       channel_offset=template["Channel offset"], data_type=template["Type"],
                       direction=template["Direction"], member_name=path.split(".")[2], dip1500_register=True)
            row["GVL_COM variable"] = path
            row["GVL_MODBUS variable"] = f"GVL_MODBUS.{name}_{bank}"
            added.append(row)
        state.setdefault("rtu_channels", []).extend(added)
    else:
        raise ValueError("Choisis Modbus TCP ou RTU.")
    if include:
        membership = freeze_membership(state)
        for block in ("GFC_COM_IN", "GFC_COM_OUT"):
            if name not in membership[block]:
                membership[block].append(name)
    return added


def regularize(state):
    typ = next((row for row in state.get("types", []) if row.get("name") == "DIP1500"), None)
    if typ is None:
        return False
    original = typ.get("fb", {})
    if (state.get("st_overrides") or {}).get(original.get("name"), {}).get("body") is not None:
        return False
    fb = copy.deepcopy(original)
    flow = ensure_flow(fb)
    if flow.get("section_overrides"):
        return False
    rows = flow_view(fb)["mappings"]
    options = {row["path"]: str(row["iec_type"]).upper() for row in variable_options(typ, state)}
    lines = flow.get("processing_code", "").splitlines(keepends=True)
    depth = 0
    removed = set()
    additions = []
    for index, line in enumerate(lines):
        clean = _st_code(line).strip()
        match = ASSIGN.fullmatch(line) if depth == 0 else None
        if match:
            _, destination, source = match.groups()
            section = None
            if re.fullmatch(r"COM\.COM_INPUT_IR\[\d+\]", source) and destination.startswith("INTERN."):
                section = "inputs"
            elif re.fullmatch(r"COM\.COM_OUTPUT_COILS\[\d+\]", destination) and source.startswith("INTERN."):
                section = "outputs"
            if section and options.get(source) and options[source] == options.get(destination):
                affected = lines[:index] if section == "inputs" else lines[index + 1:]
                context = _st_code("".join(affected))
                # A copy may move only past code independent of both its endpoints.
                def used(path):
                    base, bracket, index = path.partition("[")
                    suffix = rf"(?:\s*\[\s*{re.escape(index[:-1])}\s*\]|(?!\s*\[))" if bracket else ""
                    return re.search(r"(?<![\w.])" + re.escape(base) + r"(?!\w)" + suffix, context, re.I)
                references = any(used(path) for path in (source, destination)) or re.search(r"\bRETURN\b", context, re.I)
                duplicate = any(row["section"] == section and row["destination"] == destination for row in rows)
                if not references and not duplicate:
                    removed.add(index)
                    additions.append({"section": section, "source": source, "destination": destination, "original": line})
        if re.match(r"^(IF|FOR|WHILE|CASE|REPEAT)\b", clean, re.I):
            depth += 1
        elif re.match(r"^(END_IF|END_FOR|END_WHILE|END_CASE|UNTIL)\b", clean, re.I):
            depth = max(0, depth - 1)
    if not additions:
        return False
    proposed = {**typ, "fb": fb}
    processing = "".join(line for index, line in enumerate(lines) if index not in removed)
    if update_flow(proposed, state, mappings=rows + additions, processing_code=processing):
        return False
    flow = proposed["fb"]["flow"]
    for row in additions:
        segments = flow[f"{row['section'][:-1]}_segments"]
        segment = next(item for item in segments if item.get("destination") == row["destination"])
        segment["original"] = row["original"]
    proposed["fb"]["body"] = render_flow(flow)
    typ["fb"] = proposed["fb"]
    return True


def migrate_named_project(session, name, *, apply=False, expected_revision=None):
    from sqlalchemy import func, select
    from backend import v2_store as store, v2_workspace as workspace
    from backend.v2_hmi import is_hmi

    project = session.scalar(select(store.V2Project).where(func.lower(store.V2Project.name) == name.casefold()))
    if project is None:
        raise ValueError(f"Projet introuvable : {name}.")
    previous_id = session.info.get("workspace_id")
    session.info["workspace_id"] = project.id
    try:
        current = store.load_project(session)
        if not current or not is_hmi(current):
            raise ValueError("Ce projet doit utiliser le profil PanelView.")
        if apply and current["revision"] != expected_revision:
            raise ValueError(f"La révision a changé : {current['revision']}. Relance la vérification.")
        proposed = copy.deepcopy(current)
        if not simplify(proposed):
            return {"revision": current["revision"], "already_simplified": True}
        names = [str(row.get("name") or row.get("id")) for row in proposed["instances"]
                 if str(row.get("type_id")).casefold() == "dip1500"]
        for instance in names:
            if any(row.get("Instance") == instance for row in proposed.get("rtu_slaves", [])):
                configure_instance(proposed, instance, "rtu")
            configure_instance(proposed, instance, "tcp", include=True)
        _sync_modbus(proposed)
        baseline = set(workspace._compatibility_errors(current))
        issues = [error for error in workspace._compatibility_errors(proposed) if error not in baseline]
        if issues:
            raise ValueError(issues[0])
        summary = {"revision": current["revision"], "rtu": {}, "tcp": {}}
        for name in names:
            summary["rtu"][name] = sum(row.get("Instance") == name for row in proposed["rtu_channels"])
            summary["tcp"][name] = {}
            for row in proposed["tcp_points"]:
                if row.get("Instance") == name:
                    summary["tcp"][name].setdefault(row["table"], []).append(row["Modbus address"])
        # Exercise the same catalogue and validation checks as a real save.
        with session.begin_nested() as review:
            session.info["version_event"] = {"kind": "dip1500", "comment": "DIP1500 simplifié et mappages Modbus RTU/TCP"}
            saved = store.save_project(session, proposed, username="system:DIP1500", expected_revision=current["revision"])
            if apply:
                summary["saved_revision"] = saved["revision"]
            else:
                review.rollback()
        return summary
    finally:
        if previous_id is None:
            session.info.pop("workspace_id", None)
        else:
            session.info["workspace_id"] = previous_id


if __name__ == "__main__":
    import argparse
    from sqlalchemy.orm import Session
    from backend.db import database_url, make_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--apply-revision", type=int)
    args = parser.parse_args()
    with Session(make_engine(database_url())) as session:
        result = migrate_named_project(session, args.project, apply=args.apply_revision is not None,
                                       expected_revision=args.apply_revision)
        if args.apply_revision is not None:
            session.commit()
        print(json.dumps(result, ensure_ascii=False))
