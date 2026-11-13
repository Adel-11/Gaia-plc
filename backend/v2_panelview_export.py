"""Export the active Modbus TCP map as a PanelView External Tags workbook."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from backend.v2_addressing import BASES
from backend.v2_configuration import IEC_INTEGER_BOUNDS
from backend.v2_manual_comm import DOUBLE_WORD_TYPES


TEMPLATE = Path(__file__).with_name("panelview-external-tags.xlsx")
SHEET = "xl/worksheets/sheet1.xml"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ADDRESS_OFFSET = {"input_registers": 30000, "discrete_inputs": 0, "coils": 0, "holding_registers": 40000}
TYPES = {
    "BOOL": "Boolean", "REAL": "Real",
    "SINT": "8 bit integer", "BYTE": "Unsigned 16 bit integer", "USINT": "Unsigned 16 bit integer",
    "INT": "16 bit integer", "WORD": "Unsigned 16 bit integer", "UINT": "Unsigned 16 bit integer",
    "DINT": "32 bit integer", "DWORD": "Unsigned 32 bit integer", "UDINT": "Unsigned 32 bit integer",
}
# PanelView displays IEC time/date encodings as their underlying numeric value.
TYPES.update(dict.fromkeys(DOUBLE_WORD_TYPES - TYPES.keys(), "Unsigned 32 bit integer"))


def has_panelview(state):
    return any(str(client.get("Client") or client.get("id") or "").casefold() == "hmi"
               for client in state.get("tcp_clients", []))


def _tags(state, controller):
    signals = defaultdict(list)
    occupied = set()
    for point in state.get("tcp_points", []):
        if str(point.get("Client") or point.get("client_id") or "").casefold() != "hmi":
            continue
        table = str(point.get("table") or point.get("sheet") or "").casefold()
        raw = point.get("Modbus address", point.get("address"))
        path = str(point.get("GVL_COM variable") or "")
        match = re.fullmatch(r"GVL_COM\.COM_(\w+)\.(.+)", path, re.I)
        kind = str(point.get("Type") or point.get("data_type") or "").upper()
        if not match or table not in ADDRESS_OFFSET or not re.fullmatch(r"\d+", str(raw)):
            raise ValueError(f"Point Modbus HMI incomplet : {path or point.get('Name') or raw}.")
        address = int(raw)
        if not BASES[table] <= address < BASES[table] + 9999 or (table, address) in occupied:
            raise ValueError(f"Adresse Modbus HMI hors plage ou en double : {table} {address}.")
        occupied.add((table, address))
        if kind not in TYPES or (kind == "BOOL") != (table in {"coils", "discrete_inputs"}):
            raise ValueError(f"Type {kind or '?'} incompatible avec {table} pour {path}.")
        member = re.sub(r"\.Word_value\[\d+\]$", "", match[2], flags=re.I)
        name = re.sub(r"[^A-Za-z0-9_]+", "_", f"{match[1]}_{member}").rstrip("_")
        word = str(point.get("Word") or point.get("word") or "").upper()
        signals[(table, match[1].casefold(), member.casefold(), kind)].append((address, name, word))

    tags = []
    for (table, _, _, kind), records in signals.items():
        records.sort()
        if kind in DOUBLE_WORD_TYPES:
            if len(records) != 2 or records[1][0] != records[0][0] + 1 or {r[2] for r in records} != {"LSW", "MSW"}:
                raise ValueError(f"Deux registres consécutifs LSW/MSW sont requis pour {records[0][1]}.")
            records = records[:1]
        elif any(record[2] for record in records):
            raise ValueError(f"Mot LSW/MSW inattendu pour {records[0][1]}.")
        tags.extend((table, address, name, kind) for address, name, _ in records)

    used_names = set()
    for table, address, name, kind in sorted(tags, key=lambda tag: (tuple(ADDRESS_OFFSET).index(tag[0]), tag[1])):
        reference = str(address + ADDRESS_OFFSET[table])
        unique_name = name
        while unique_name.casefold() in used_names:
            unique_name += "_" + reference
        used_names.add(unique_name.casefold())
        bounds = (0, 0) if kind == "BOOL" else (-9999999, 9999999) if kind == "REAL" else IEC_INTEGER_BOUNDS.get(kind, (0, 4294967295))
        yield (unique_name, TYPES[kind], reference, controller, "", str(bounds[0]), str(bounds[1]),
               "Read/Write", "500", "False", "1", "2", "1", "2")


def export_panelview(state, controller="PLC-1"):
    controller = controller.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,63}", controller):
        raise ValueError("Saisis le nom du contrôleur configuré dans l’IHM, par exemple PLC-1.")
    output = BytesIO()
    with ZipFile(TEMPLATE) as template, ZipFile(output, "w") as workbook:
        root = etree.fromstring(template.read(SHEET))
        data = root.find(NS + "sheetData")
        for number, values in enumerate(_tags(state, controller), start=3):
            row = etree.SubElement(data, NS + "row")
            for column, value in zip("ABCDEFGHIJKLMN", values):
                cell = etree.SubElement(row, NS + "c", r=f"{column}{number}", t="str")
                etree.SubElement(cell, NS + "v").text = value
        for entry in template.infolist():
            content = etree.tostring(root, encoding="utf-8", xml_declaration=True) if entry.filename == SHEET else template.read(entry)
            workbook.writestr(entry, content)
    return output.getvalue()
