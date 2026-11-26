"""Remove an instance's project configuration before rebuilding its PLC objects."""
import re

from backend.v2_gfc_mappings import instance_name, reconcile_copy_choices
from backend.v2_io_copies import variable_rows
from backend.v2_program_membership import freeze_membership


def delete_instance(state, instance):
    from copy import deepcopy
    from backend.v2_instance_editor import _sync_workbooks

    previous = deepcopy(state)
    name = str(instance.get("name") or instance["id"])
    aliases = {str(instance.get("id") or name).casefold(), name.casefold()}

    def owned(row):
        return instance_name(row).casefold() in aliases

    freeze_membership(state)
    removed = {key: [row for row in state.get(key, []) if owned(row)]
               for key in ("tcp_points", "rtu_channels", "rtu_slaves", "pa_values")}
    slave_ids = {str(row.get("id")).casefold() for row in removed["rtu_slaves"] if row.get("id")}
    removed["rtu_channels"] = [row for row in state.get("rtu_channels", [])
                               if owned(row) or str(row.get("slave_id") or "").casefold() in slave_ids]
    io = [(collection, row, key) for collection, row, key in variable_rows(state) if owned(row)]
    remaining_iomap = {key for _, row, key in variable_rows(state) if not owned(row)}
    iomap = {key for _, _, key in io if key not in remaining_iomap}
    modbus = {str(row.get("GVL_MODBUS variable") or "").removeprefix("GVL_MODBUS.").split("[")[0]
              for row in removed["rtu_channels"]}
    deleted_channels = {id(row) for row in removed["rtu_channels"]}
    still_mapped = {str(row.get("GVL_MODBUS variable") or "").removeprefix("GVL_MODBUS.").split("[")[0]
                    for row in state.get("rtu_channels", []) if id(row) not in deleted_channels}
    modbus = {key for key in modbus - still_mapped if re.fullmatch(r"[A-Za-z_]\w*", key)}
    state["instances"] = [row for row in state["instances"] if str(row.get("name") or row.get("id")).casefold() not in aliases]
    for key in removed:
        deleted = {id(row) for row in removed[key]}
        state[key] = [row for row in state.get(key, []) if id(row) not in deleted]
    physical = sum(owned(row) for row in state.get("io_channels", []))
    state["virtual_io"] = [row for row in state.get("virtual_io", []) if not owned(row)]
    for row in state.get("io_channels", []):
        if owned(row):
            prior = row.pop("_assignment_prior_status", None)
            for field in ("Instance", "instance_id", "instance", "GVL_IO member", "member_name", "member_path"):
                row[field] = None
            if prior is not None:
                row["Status"] = prior
            path = str(row.get("GVL_IOMAP variable") or "").removeprefix("GVL_IOMAP.").split("[")[0]
            if path.casefold() in iomap:
                row["GVL_IOMAP variable"] = row["variable"] = None
                row["Status"] = prior if str(prior or "").casefold() == "spare" else "SPARE"
    for key, values in (("removed_iomap_variables", iomap), ("removed_modbus_variables", modbus)):
        state[key] = sorted(set(state.get(key, [])) | values)
    for gvl, names in (("GVL_IOMAP", iomap), ("GVL_MODBUS", {key.casefold() for key in modbus})):
        folders = state.get("gvl_folders", {}).get(gvl, {})
        for key in names:
            folders.pop(key, None)
    for members in state["program_membership"].values():
        members[:] = [member for member in members if str(member).casefold() not in aliases]

    def connected(call):
        return any(str(call.get(field) or "").casefold() in aliases
                   for field in ("instance_a_id", "instance_b_id", "Instance A", "Instance B",
                                 "instance_a", "instance_b", "source_instance_id", "destination_instance_id"))

    calls = [call for call in state.get("exchange_calls", []) if connected(call)]
    state["exchange_calls"] = [call for call in state.get("exchange_calls", []) if not connected(call)]
    from backend.v2_special_exc import KEY as SPECIAL_EXC
    special = state.get(SPECIAL_EXC, [])
    kept_special = [row for row in special if not owned(row) and not (_instance_symbols(name) & _identifiers(row.get('source', '')))]
    if SPECIAL_EXC in state: state[SPECIAL_EXC] = kept_special
    for alias in aliases:
        nodes = state.get("exchange_layout", {}).get("nodes", {})
        for key in list(nodes):
            if key.casefold() == alias:
                nodes.pop(key)
    reconcile_copy_choices(previous, state)
    for choices in state.get("gfc_copy_overrides", {}).values():
        for key, target in list(choices.items()):
            if _instance_symbols(name) & _identifiers(target):
                choices.pop(key)
    hardware_count = _clear_hardware(state, name, iomap, modbus)
    for gvl, names in (("GVL_IOMAP", iomap), ("GVL_MODBUS", modbus)):
        for variable in names:
            state.get("object_folders", {}).pop(f"{gvl}.{variable}", None)
    _sync_workbooks(state)
    from backend.v2_configuration import synchronize_io_workbook, synchronize_workbooks
    synchronize_workbooks(state)
    synchronize_io_workbook(state)
    from backend.v2_engine import _mapped_vars
    state["mapped_variables"] = _mapped_vars(state)
    _check_surviving_code(state, name, iomap, modbus)
    return [
        f"Supprimer l’instance {name} et ses déclarations GVL, appels GFB et recopies GFC.",
        f"Libérer {len(removed['tcp_points'])} adresses TCP, {len(removed['rtu_channels'])} registres RTU et {len(removed['rtu_slaves'])} adresse(s) esclave.",
        f"Retirer {len(calls)} connexion(s) Exchange et {len(removed['pa_values'])} valeur(s) PA.",
        f"Retirer {len(special) - len(kept_special)} affectation(s) EXC particulière(s).",
        f"Retirer {len(iomap)} variable(s) IOMAP, {len(modbus)} variable(s) Modbus PLC et {hardware_count} liaison(s) du rack. Libérer {physical} affectation(s) E/S.",
    ]


def _instance_symbols(name):
    return {f"{prefix}_{name}{suffix}".casefold()
            for prefix, suffix in (("HMI", ""), ("COM", ""), ("IO", ""), ("PA", ""), ("EXC", ""), ("FB", ""), ("FB", "_1"))}


def _identifiers(text):
    from backend.v2_type_deletion import code_identifiers
    return code_identifiers(text)


def _clear_hardware(state, name, iomap, modbus):
    from lxml import etree
    from backend.v2_io_hardware import STATE_KEY, _xml_bytes

    hardware = state.get(STATE_KEY)
    if not hardware:
        return 0
    root = etree.fromstring(hardware["xml"].encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    count = 0
    for mapping in root.xpath('//*[local-name()="Mapping"]'):
        for node in mapping.iter():
            value = (node.text or "").strip()
            parts = value.casefold().split(".")
            affected = (
                len(parts) >= 2 and parts[-2] == "gvl_iomap" and parts[-1].split("[")[0] in iomap or
                any(part in _instance_symbols(name) for part in parts) or
                len(parts) >= 2 and parts[-2] == "gvl_modbus" and parts[-1].split("[")[0] in {key.casefold() for key in modbus}
            )
            if affected:
                node.text = ""
                count += 1
    if count:
        state[STATE_KEY] = {**hardware, "xml": _xml_bytes(root).decode()}
    return count


def _check_surviving_code(state, name, iomap, modbus):
    from backend.v2_plc import build_program, NS
    from backend.v2_plc_validation import _st_code

    symbols = _instance_symbols(name)
    mappings = {("GVL_IOMAP", key.casefold()) for key in iomap} | {("GVL_MODBUS", key.casefold()) for key in modbus}
    removed_globals = {"GVL_IOMAP": iomap, "GVL_MODBUS": {key.casefold() for key in modbus}}
    for label, obj in build_program(state).items():
        parameters = {variable.get("name", "").casefold() for variable in obj["element"].xpath(
            './p:interface/p:inputVars/p:variable | ./p:interface/p:outputVars/p:variable | ./p:interface/p:inOutVars/p:variable',
            namespaces={"p": NS[1:-1]})}
        # Documentation is descriptive. Only the effective source/declaration can block deletion.
        source = "\n".join(obj["element"].xpath(
            './/*[local-name()="ST" or local-name()="InterfaceAsPlainText"]/*[local-name()="xhtml"]/text()'))
        source += "\n" + "\n".join(obj["element"].xpath('.//*[local-name()="simpleValue"]/@value'))
        if (symbols - parameters) & _identifiers(source):
            raise ValueError(f"{label} référence encore {name} dans du ST personnalisé. Retire cette référence avant de supprimer l’instance.")
        code = _st_code(source)
        if re.search(r"\bGVL_(?:COM|HMI|IO|PA|EXC)\s*\.\s*(?:" + "|".join(map(re.escape, symbols)) + r")\b", code, re.I):
            raise ValueError(f"{label} référence encore {name} dans du ST personnalisé. Retire cette référence avant de supprimer l’instance.")
        for gvl, variable in mappings:
            if re.search(r"\b" + re.escape(gvl) + r"\s*\.\s*" + re.escape(variable) + r"\b", code, re.I):
                raise ValueError(f"{label} utilise encore {gvl}.{variable} dans du ST personnalisé.")
        for variable in obj["element"].iter(NS + "variable"):
            if variable.get("name", "").casefold() in symbols - parameters:
                raise ValueError(f"{label} déclare encore une variable de {name} dans du ST personnalisé.")
            if variable.get("name", "").casefold() in removed_globals.get(label, set()):
                raise ValueError(f"{label} déclare encore {variable.get('name')} dans du ST personnalisé.")
