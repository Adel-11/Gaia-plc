"""Build the PanelView-only reference model from the validated CODESYS export."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re

from lxml import etree

from backend import v2_codesys as codesys, v2_engine as engine
from backend.v2_addressing import BASES
from backend.v2_plc import NS

PROFILE = "hmi-v8"
REFERENCE = Path(__file__).resolve().parents[1] / "reference-v2/v8/latest_codesys.xml"
RETIRED = re.compile(r"FUXA|WEB\s*VISU|\bSCADA(?:_|\b)|FLOAT32MLE", re.I)
RETIRED_TYPES = {"UPI_LOCAL_FUXA", "UPI_LOCAL_WEBVISU"}
TCP_ARRAYS = {
    "Coils_TCP_HMI": "coils",
    "Discret_input_TCP_HMI": "discrete_inputs",
    "Input_register_TCP_HMI": "input_registers",
    "Holding_register_TCP_HMI": "holding_registers",
}
TCP_COPY = re.compile(
    r"^\s*(GVL_\S+)\s*:=\s*(GVL_\S+);\s*//\s*(\d+)\s*\|\s*(.+)$",
    re.M,
)
TCP_ARRAY = re.compile(r"GVL_MODBUS\.(\w+)\[(\d+)\](?:\.(\d))?")


def is_hmi(state):
    return (state or {}).get("program_profile") == PROFILE


def member_error(state, role, name):
    if is_hmi(state) and role == "COM" and str(name).upper().startswith(("SCADA_", "WEBVISU_")):
        return f"{name}: use HMI_ for PanelView signals."
    return None


def _rows(book, name, records):
    sheet = engine._sheet_map(book)[name]
    sheet["rows"] = [[row.get(col) for col in sheet["headers"]] for row in records]


def _iec_type(node):
    local = etree.QName(node).localname
    if local == "derived":
        return node.get("name")
    if local == "array":
        dimension = node.find(NS + "dimension")
        base = node.find(NS + "baseType/*")
        return f"ARRAY[{dimension.get('lower')}..{dimension.get('upper')}] OF {_iec_type(base)}"
    return local.upper()


def _type_book(book, typ, objects, tcp_points):
    for dut in typ["duts"]:
        sheet_name = f"DUT_{dut['role']}"
        old = {row["Member"]: row for row in engine._objects(book, sheet_name) if row.get("Member")}
        records = []
        node = objects[dut["name"]]
        for variable in node.findall(f"{NS}baseType/{NS}struct/{NS}variable"):
            name = variable.get("name")
            row = dict(old.get(name, {}))
            documentation = variable.find(NS + "documentation")
            comment = "".join(documentation.itertext()).strip() if documentation is not None else ""
            section = re.match(r"^\[([^\]]+)\]\s*", comment)
            if section:
                comment = comment[section.end():]
            row.update(Member=name, Type=_iec_type(variable.find(NS + "type/*")),
                       Section=section[1] if section else None, Comment=comment or None)
            simple = variable.find(f"{NS}initialValue/{NS}simpleValue")
            row["Default value"] = simple.get("value") if simple is not None else None
            if RETIRED.search(str(row.get("Exchange") or "")):
                row["Exchange"] = None
            records.append(row)
        _rows(book, sheet_name, records)
    for name in ("TEMPLATE_TCP", "TEMPLATE_EXC"):
        if name not in engine._sheet_map(book):
            continue
        if name == "TEMPLATE_EXC":
            records = [r for r in engine._objects(book, name) if "UPI_LOCAL_HMI" in str(r.get("FC", ""))]
        else:
            instances = {f"{typ['name']}_1", typ["name"]}
            records = []
            for point in tcp_points:
                if point["Instance"] not in instances:
                    continue
                row = dict(point)
                row["GVL_COM variable"] = row["GVL_COM variable"].replace(
                    "COM_" + row["Instance"] + ".", "COM_<INSTANCE>.", 1)
                row.update({"Instance": "<INSTANCE>", "Modbus address": None,
                            "Client address": None, "GVL_MODBUS variable": None,
                            "Comment": "Paste in " + row["table"].upper()})
                records.append(row)
        _rows(book, name, records)


def _tcp_points(objects, legacy):
    # Preserve engineering labels/units and word metadata from the old HMI sheet.
    old = {p["GVL_COM variable"]: p for p in legacy["tcp_points"] if p["Client"] == "HMI"}
    points = []
    seen = set()
    for block in ("GFC_COM_IN", "GFC_COM_OUT"):
        inbound = block == "GFC_COM_IN"
        for left, right, address, name in TCP_COPY.findall(codesys._st(objects[block])):
            com, mapped = (left, right) if inbound else (right, left)
            match = TCP_ARRAY.fullmatch(mapped)
            if match is None or match[1] not in TCP_ARRAYS:
                continue
            table = TCP_ARRAYS[match[1]]
            index = int(match[2])
            address = int(address)
            decoded = BASES[table] + (index * 8 + int(match[3]) if match[3] is not None else index)
            if decoded != address or (table, address) in seen:
                raise ValueError(f"Reference mismatch at {block}: {table} {address} ({mapped})")
            seen.add((table, address))
            if com not in old:
                raise ValueError(f"No engineering metadata for reference point {com}")
            row = dict(old[com])
            row.update({"Modbus address": address, "Client address": address,
                        "Name": re.sub(r"\s*\((?:low|high) word\)$", "", name.strip(), flags=re.I),
                        "GVL_MODBUS variable": mapped, "GVL_COM variable": com, "table": table,
                        "Direction": "Client to PLC" if inbound else "PLC to Client"})
            points.append(row)
    counts = Counter(point["table"] for point in points)
    if counts != {"coils": 15, "discrete_inputs": 16, "input_registers": 99, "holding_registers": 13}:
        raise ValueError(f"Unexpected PanelView reference map: {dict(counts)}")
    return points


def clean_list(sheet):
    """Drop retired choices in place while keeping each validation column aligned."""
    for index, _ in enumerate(sheet["headers"]):
        values = [row[index] for row in sheet["rows"] if index < len(row) and row[index] is not None
                  and not RETIRED.search(str(row[index]))]
        for row_index, row in enumerate(sheet["rows"]):
            if index < len(row):
                row[index] = values[row_index] if row_index < len(values) else None


def clean_template(workbook):
    """Keep the original workbook's formatting and dropdown ranges for V8."""
    replacements = (
        ("COM_ (Modbus RTU), SCADA_ (FUXA), HMI_ (local HMI), WEBVISU_ (WebVisu)",
         "COM_ (Modbus RTU), HMI_ (PanelView 800)"),
        ("FUXA : discrete inputs 100001.., others = Modbus address.", "Use the Modbus address on PanelView."),
        (" (FUXA uses 100001.. for discrete inputs)", ""),
        ("Used as tag name in FUXA and as label in the supervisions.", "Used as a label on the PanelView."),
        ("Modbus TCP (FUXA / HMI), Modbus RTU, WebVisu", "Modbus TCP (PanelView), Modbus RTU"),
        ("EXC_UPI_LOCAL_FUXA", "EXC_UPI_LOCAL_HMI"),
        ("UPI_INPUT_GLOBAL_LOCAL_FUXA", "UPI_INPUT_GLOBAL_LOCAL_HMI"),
        ("GVL_MODBUS index = address - 1 (discrete inputs : address - 10001).",
         "Coils and discrete inputs use byte index and bit. Registers use index address - 1."),
    )

    def revise(text):
        for before, after in replacements:
            text = text.replace(before, after)
        return text

    if "LISTS" in workbook:
        choices = workbook["LISTS"]
        for cells in choices.iter_cols(min_row=2):
            values = [cell.value for cell in cells if cell.value is not None and not RETIRED.search(str(cell.value))]
            for index, cell in enumerate(cells):
                cell.value = values[index] if index < len(values) else None
    for sheet in workbook:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.comment:
                    note = revise(cell.comment.text)
                    if cell.value == "Changed from supervision":
                        note = "Whether the equipment FB allows this PA parameter to be changed from the PanelView."
                    cell.comment.text = note
                if sheet.title == "README" and isinstance(cell.value, str):
                    line = revise(cell.value)
                    cell.value = None if RETIRED.search(line) else line
    return workbook


@lru_cache(maxsize=1)
def load_hmi_pack():
    legacy = engine.load_pack()
    xml = REFERENCE.read_bytes()
    root = codesys._safe_parse(xml)
    objects = codesys._objects(root)
    snapshot = engine._snapshot_metadata(xml.decode("utf-8-sig"), "Gaia V7.xml")
    books = deepcopy(legacy["workbooks"])
    tcp = _tcp_points(objects, legacy)
    for typ in legacy["types"]:
        filename = typ["source_file"]
        if typ["fb"]["name"] not in objects:
            books.pop(filename)
        else:
            _type_book(books[filename], typ, objects, tcp)
    tcp_book = books["MODBUS_TCP.xlsx"]
    panel = next(row for row in engine._objects(tcp_book, "CLIENTS") if row["Client"] == "HMI")
    panel["Address range"] = "Coils 1-1504, DI 10001-11504, IR 1-1501, HR 1-1501"
    _rows(tcp_book, "CLIENTS", [panel])
    for name, table in engine.TABLES.items():
        _rows(tcp_book, name, sorted((r for r in tcp if r["table"] == table),
                                    key=lambda r: r["Modbus address"]))
    exc = books["EXCHANGES_EXC.xlsx"]
    for name in ("FC_EXC", "CALLS"):
        _rows(exc, name, [row for row in engine._objects(exc, name) if row["FC"] in objects])
    exc["sheets"] = [sheet for sheet in exc["sheets"] if not RETIRED.search(sheet["name"])]
    for book in books.values():
        for sheet in book["sheets"]:
            if sheet["name"] == "LISTS":
                clean_list(sheet)
            elif sheet["name"] == "README":
                sheet["rows"] = [row for row in sheet["rows"] if not any(RETIRED.search(str(v)) for v in row)]
    state = engine.parse_project(books, snapshot)
    state["program_profile"] = PROFILE
    state["functions"] = deepcopy(legacy["functions"])
    for function in state["functions"]:
        function["body"] = codesys._st(objects[function["name"]])
    folders = codesys._folders(root, objects)
    for typ in state["types"]:
        typ["fb"]["body"] = codesys._st(objects[typ["fb"]["name"]])
        typ["folder"] = folders.get(typ["fb"]["name"], typ["folder"])
    return state


def upgrade_named_project(session, name):
    """Switch one existing project atomically and pin its prior revision as an archive."""
    from sqlalchemy import func, select
    from backend import v2_store as store, v2_workspace as workspace
    from backend.v2_fb_flow import convert_project

    project = session.scalar(select(store.V2Project).where(func.lower(store.V2Project.name) == name.casefold()))
    if project is None:
        return False
    _, raw = workspace._raw(session, project.id)
    if raw is None or is_hmi(raw):
        return False
    replacement = deepcopy(load_hmi_pack())
    convert_project(replacement)
    errors = workspace._compatibility_errors(replacement)
    if errors:
        raise ValueError("Invalid PanelView reference: " + "; ".join(errors[:5]))

    revision = workspace._lock(session)
    session.refresh(project)
    _, previous = workspace._raw(session, project.id)
    if project.name.casefold() != name.casefold() or is_hmi(previous):
        return False
    library = workspace.catalog_id(replacement)
    catalog = session.get(workspace.SharedCatalog, library)
    if catalog is not None:
        raise ValueError("A different PanelView catalog already exists")
    session.add(workspace.SharedCatalog(id=library, revision=1, payload=workspace.dump(workspace._catalog(replacement))))
    replacement.update(project_name=project.name, v7_archive_revision=project.current_revision_id)
    if previous.get("generic_objects"):
        replacement["generic_objects"] = deepcopy(previous["generic_objects"])
    workspace._persist(session, project.id, replacement, previous, "system:panelview-v8", revision, {
        "kind": "program_import",
        "name": replacement["snapshot"]["filename"],
        "digest": workspace.program_digest(replacement),
    })
    return True
