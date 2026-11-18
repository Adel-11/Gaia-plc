"""Excel editing of unsaved type variables; importing never persists a project."""
from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from uuid import uuid4

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import BaseModel, Field, ValidationError

from backend import v2_store as store
from backend.v2_generic_objects import ANALOG_TYPES, MODBUS_TYPES, Signal, Variables, _declared
from backend.v2_modbus_templates import Mapping, validate_rows
from backend.v2_type_creation import IEC_SCALARS, ROLES, _identifier, _iec_type_error

FORMAT = "gaia-type-variables/1"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES = 8 * 1024 * 1024
SECTIONS = ["INPUT (field to plc)", "OUTPUT (plc to field)", "STATE", "PARAMETERS", "INTERNAL"]
PREFIXES = ["INPUT", "OUTPUT", "STATE", "CMD", "SPARE", "COM", "HMI"]
FB_TYPES = ["TON", "TOF", "TP", "R_TRIG", "F_TRIG", "CTU", "CTD", "CTUD", "DUT_Union_Real_Word"]
# field, visible heading, explanation, choice list
MEMBER_COLUMNS = [
    ("prefix", "Prefix", "Optional prefix. The generated member name is Prefix_Name.", "Prefixes"),
    ("name", "Name", "IEC member name without the optional prefix. Row order is preserved.", None),
    ("iec_type", "IEC type", "IEC scalar, FB or known DUT. Arrays and sized strings may also be typed manually.", "IEC_Types"),
    ("section", "Comment group", "Comment separator in the DUT. It does not declare VAR_INPUT or VAR_OUTPUT.", "Sections"),
    ("default_value", "Default", "Initial ST value. Leave blank to use the IEC default. FALSE and zero are preserved.", None),
    ("unit", "Unit", "Engineering unit.", None),
    ("comment", "Comment", "Documentation emitted with the declaration.", None),
    ("id", "ID", "Keep this identity when renaming an inherited member. Leave blank for a new member.", None),
]
SIGNAL_COLUMNS = [
    ("name", "Name", "Signal name. Creates the associated IO/COM, INT and (for inputs) EXC members.", None),
    ("direction", "Direction", "input = field to PLC. output = PLC to field.", "Directions"),
    ("kind", "Signal kind", "digital requires BOOL. analog uses the supported numeric types.", "Signal_Kinds"),
    ("iec_type", "IEC type", "Choose a type compatible with the signal kind and protocol.", "Signal_Types"),
    ("unit", "Unit", "Engineering unit.", None),
    ("comment", "Comment", "Documentation for the generated members.", None),
    ("io_name", "IO or COM name", "Optional explicit IO/COM member name. Blank uses INPUT_/OUTPUT_ and the signal name.", None),
    ("internal_name", "INT name", "Optional explicit INT member name. Blank uses the signal name.", None),
    ("exchange_name", "EXC name", "Optional EXC member name for an input. Blank uses OUTPUT_ and the signal name.", None),
    ("id", "ID", "Keep this identity when renaming an inherited signal. Leave blank for a new signal.", None),
]
MAPPING_COLUMNS = [
    ("table", "Table", "Modbus address table. Addresses in read-only tables have a fixed direction.", "Tables"),
    ("member", "COM member", "COM member path without COM. A union REAL uses Member.Real_value.", "COM_Members"),
    ("inbound", "Direction", "inbound = client/equipment to PLC. outbound = PLC to client/equipment.", "Mapping_Directions"),
    ("register", "RTU register", "Manufacturer register, zero based, decimal or 0x hexadecimal. TCP addresses are assigned on instances.", None),
    ("order", "Word order", "LSW_FIRST = low word first. MSW_FIRST = high word first. REAL and 32-bit formats use two registers.", "Word_Orders"),
    ("bank", "PLC bank", "Optional existing RTU array slot, e.g. Device_Registers[0]. Usually leave blank.", None),
    ("bank_type", "PLC bank type", "Only when preserving an existing RTU bank, e.g. ARRAY[0..9] OF WORD.", None),
    ("function", "Function", "Optional existing RTU function code. Usually leave blank.", None),
    ("channel", "Channel", "Optional existing RTU channel label. Usually leave blank.", None),
]


class Draft(BaseModel):
    name: str = Field(default="", max_length=128)
    kind: str = "Equipment"
    generic_template_id: str | None = None
    signals: list[dict] = Field(default_factory=list, max_length=100)
    duts: list[dict] = Field(default_factory=list, max_length=5)
    modbus_template: list[dict] = Field(default_factory=list, max_length=1000)


def _choices(state, draft):
    from backend.v2_modbus_templates import _members

    options = state.get("catalog_options") or {}
    types = sorted(IEC_SCALARS) + FB_TYPES + [
        dut["name"] for typ in state.get("types", []) for dut in typ.get("duts", []) if dut.get("name")]
    if _identifier(draft.name):
        types += [f"DUT_{role}_{draft.name}" for role in ROLES.get(draft.kind, ROLES["Equipment"])]
    try:
        members = list(_members(_combined_type(state, draft), state))
    except (ValueError, ValidationError, KeyError):
        members = [str(m.get("name", "")) for dut in draft.duts if dut.get("role") == "COM" for m in dut.get("members", []) if m.get("name")]
    return {
        "IEC_Types": list(dict.fromkeys(types)), "Signal_IO": sorted(ANALOG_TYPES),
        "Signal_Modbus": sorted(MODBUS_TYPES), "Signal_Digital": ["BOOL"],
        "Directions": ["input", "output"], "Signal_Kinds": ["analog", "digital"],
        "Prefixes": list(dict.fromkeys(PREFIXES + options.get("prefixes", []))),
        "Sections": list(dict.fromkeys(SECTIONS + options.get("sections", []))),
        "Tables": ["coils", "discrete_inputs", "input_registers", "holding_registers"],
        "Mapping_Directions": ["inbound", "outbound"], "Word_Orders": ["LSW_FIRST", "MSW_FIRST"],
        "COM_Members": members or [""],
    }


def _cell(cell, value):
    cell.value = value
    # ST literals starting with '=' must remain text, never spreadsheet formulas.
    if isinstance(value, str):
        cell.data_type = "s"


def _sheet(wb, name, columns, rows, *, transport=None):
    ws = wb.create_sheet(name)
    ws.freeze_panes = "C2" if name.startswith("DUT_") else "B2"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_properties.tabColor = "78998A" if name.startswith("DUT_") else "B7A27A"
    for col, (_, title, description, choice) in enumerate(columns, 1):
        head = ws.cell(1, col, title)
        head.font = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
        head.fill = PatternFill("solid", fgColor="425C50")
        head.comment = Comment(description, "Gaia PLC Generator")
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = 39 if title in {"Comment", "IO or COM name", "COM member", "Comment group"} else 25
        if title == "ID":
            ws.column_dimensions[letter].hidden = True
        if choice:
            if choice == "Signal_Types":
                family = "Signal_IO" if transport == "io" else "Signal_Modbus"
                formula = f'IF($C2="digital",Signal_Digital,{family})'
            else:
                formula = choice
            custom = choice in {"IEC_Types", "Sections", "Prefixes", "COM_Members"}
            validation = DataValidation(type="list", formula1=formula, allow_blank=True,
                                        errorStyle="warning" if custom else "stop", showErrorMessage=True,
                                        showInputMessage=True, showDropDown=False)
            validation.errorTitle = "Valeur à vérifier" if custom else "Choix invalide"
            validation.error = "Choisissez dans la liste. Les types personnalisés valides restent autorisés." if custom else "Choisissez une valeur de la liste."
            validation.promptTitle = title
            validation.prompt = description[:250]
            ws.add_data_validation(validation)
            validation.add(f"{letter}2:{letter}1001")
    for rownum, row in enumerate(rows, 2):
        for col, (field, _, _, _) in enumerate(columns, 1):
            _cell(ws.cell(rownum, col), row.get(field))
    for row in ws.iter_rows(min_row=2, max_row=max(16, len(rows) + 1), max_col=len(columns)):
        for cell in row:
            cell.font = Font(name="Calibri", size=12, color="263F32")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F4F2E9")
    ws.row_dimensions[1].height = 30
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(2, len(rows) + 1)}"
    return ws


def _split_member(member, prefixes):
    name = str(member.get("name") or "")
    prefix = next((p for p in sorted(prefixes, key=len, reverse=True) if name.startswith(p + "_")), "")
    return {**member, "prefix": prefix, "name": name[len(prefix) + 1:] if prefix else name}


def export_draft(state, draft):
    if draft.kind not in ROLES:
        raise ValueError("Catégorie de type inconnue.")
    wb = Workbook()
    wb.remove(wb.active)
    choices = _choices(state, draft)
    guide = _sheet(wb, "GUIDE", [("topic", "Sujet", "", None), ("text", "Mode d’emploi", "", None)], [
        {"topic": "Un fichier par type", "text": "Remplis les feuilles de variables puis importe le classeur dans Créer un type > Excel. Les données restent modifiables avant la création."},
        {"topic": "Signaux", "text": "SIGNALS_IO, SIGNALS_TCP et SIGNALS_RTU créent les membres IO/COM, INT et EXC associés. Ne recopie pas ces membres dans les feuilles DUT : celles-ci contiennent les variables supplémentaires."},
        {"topic": "Mapping Modbus", "text": "MODBUS_TCP et MODBUS_RTU sont des modèles génériques : COM member désigne une variable COM existante. Les adresses TCP et les connexions réelles se configurent sur les instances."},
        {"topic": "Remplacement", "text": "Chaque feuille présente remplace sa partie du formulaire. Une feuille vide la vide. Une feuille supprimée laisse cette partie inchangée. EXAMPLES et GUIDE ne sont jamais importées. Le code ST et les dossiers sont conservés."},
        {"topic": "Héritage", "text": "Conserve les colonnes ID masquées lors du renommage de variables héritées. Importe dans l’assistant avec le même objet générique sélectionné. Pour une nouvelle variable, laisse ID vide."},
        {"topic": "Listes", "text": "Utilise les listes déroulantes. Pour les types complexes, tu peux saisir ARRAY[0..9] OF WORD, STRING(80) ou un DUT du projet. L’import contrôle les types et les doublons."},
        {"topic": "Aide", "text": "Survole les en-têtes pour les explications en anglais. EXAMPLES couvre les rôles DUT, les types IEC, les signaux IO/TCP/RTU, les tables et les ordres de mots."},
    ])
    guide.column_dimensions["A"].width = 26
    guide.column_dimensions["B"].width = 110
    for i in range(2, 9):
        guide.row_dimensions[i].height = 58
    _sheet(wb, "TYPE", [("key", "Field", "Context only; select identity and inheritance in the browser.", None), ("value", "Value", "Context only.", None)], [
        {"key": "format", "value": FORMAT}, {"key": "name", "value": draft.name},
        {"key": "kind", "value": draft.kind}, {"key": "generic_template_id", "value": draft.generic_template_id or ""},
    ])
    for protocol in ("io", "tcp", "rtu"):
        if draft.kind == "Equipment":
            _sheet(wb, "SIGNALS_" + protocol.upper(), SIGNAL_COLUMNS,
                   [row for row in draft.signals if row.get("transport", "io") == protocol], transport=protocol)
    for role in ROLES[draft.kind]:
        members = next((dut.get("members", []) for dut in draft.duts if dut.get("role") == role), [])
        _sheet(wb, "DUT_" + role, MEMBER_COLUMNS, [_split_member(row, choices["Prefixes"]) for row in members])
    for protocol in ("tcp", "rtu"):
        _sheet(wb, "MODBUS_" + protocol.upper(), MAPPING_COLUMNS,
               [{**row, "inbound": "inbound" if row.get("inbound") else "outbound"}
                for row in draft.modbus_template if row.get("protocol") == protocol])
    _examples(wb, choices)
    lists = wb.create_sheet("_Choices")
    for col, (name, values) in enumerate(choices.items(), 1):
        lists.cell(1, col, name)
        for row, value in enumerate(values, 2):
            _cell(lists.cell(row, col), value)
        letter = get_column_letter(col)
        wb.defined_names.add(DefinedName(name, attr_text=f"'_Choices'!${letter}$2:${letter}${max(2, len(values) + 1)}"))
    lists.sheet_state = "hidden"
    wb.active = wb.sheetnames.index("SIGNALS_IO" if draft.kind == "Equipment" else "DUT_COM")
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


def _examples(wb, choices):
    ws = wb.create_sheet("EXAMPLES")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "C4AB73"
    ws.append(["Exemples uniquement. Copie les lignes utiles vers la feuille indiquée. Cette feuille n’est jamais importée."])
    def section(title, columns, rows):
        ws.append([])
        ws.append([title])
        ws.append([col[1] for col in columns])
        for row in rows:
            ws.append([row.get(col[0]) for col in columns])
    for protocol in ("io", "tcp", "rtu"):
        rows = [{"name": f"{kind.upper()}_{direction.upper()}_{protocol.upper()}", "direction": direction, "kind": kind,
                 "iec_type": "BOOL" if kind == "digital" else "DWORD" if protocol == "io" else "REAL",
                 "comment": "Optional explicit member names; otherwise leave the name override columns blank."}
                for direction in ("input", "output") for kind in ("analog", "digital")]
        if protocol == "io":
            rows[0].update(io_name="INPUT_PRESSURE_RAW", internal_name="PRESSURE_RAW",
                           exchange_name="OUTPUT_PRESSURE_RAW", unit="counts")
        section("SIGNALS_" + protocol.upper(), SIGNAL_COLUMNS, rows)
    for role in ("PA", "COM", "EXC", "IO", "INT"):
        section("DUT_" + role, MEMBER_COLUMNS, [{"prefix": "INPUT" if role == "IO" else "", "name": "MY_VARIABLE_" + role,
                "iec_type": "REAL", "section": "PARAMETERS" if role == "PA" else "INTERNAL", "default_value": "0.0", "unit": "bar"}])
    section("DUT members: all scalar types and common complex declarations", MEMBER_COLUMNS,
            [{"name": "VALUE_" + kind, "iec_type": kind, "comment": "Choose the appropriate DUT role."} for kind in sorted(IEC_SCALARS)] + [
                {"name": "FILTER_DELAY", "iec_type": "TON", "comment": "Function block instance, usually in INT."},
                {"name": "READINGS", "iec_type": "ARRAY[0..9] OF WORD"},
                {"name": "LABEL", "iec_type": "STRING(80)", "default_value": "'Pump'"},
                {"name": "ENABLED", "iec_type": "BOOL", "default_value": False},
                {"name": "COUNTER", "iec_type": "INT", "default_value": 0},
                {"name": "DELAY", "iec_type": "TIME", "default_value": "T#5s"},
            ])
    for protocol in ("tcp", "rtu"):
        rows = []
        for i, table in enumerate(choices["Tables"]):
            inbound = (table in {"coils", "holding_registers"}) if protocol == "tcp" else (table in {"discrete_inputs", "input_registers"})
            direction = "INPUT" if inbound else "OUTPUT"
            kind = "DIGITAL" if table in {"coils", "discrete_inputs"} else "ANALOG"
            member = f"{protocol.upper()}_{direction}_{kind}_{direction}_{protocol.upper()}"
            if kind == "ANALOG":
                member += ".Real_value"
            rows.append({"table": table, "member": member, "inbound": "inbound" if inbound else "outbound",
                         "register": ("0x0006" if i == 3 else i * 2) if protocol == "rtu" else None,
                         "order": "LSW_FIRST" if i % 2 == 0 else "MSW_FIRST"})
        section("MODBUS_" + protocol.upper(), MAPPING_COLUMNS, rows)
    ws.freeze_panes = "B2"
    for col in range(1, 11):
        ws.column_dimensions[get_column_letter(col)].width = 28
    ws.column_dimensions["F"].width = 60
    for row in ws:
        for cell in row:
            cell.font = Font(name="Calibri", size=12, color="263F32")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if row[0].value and all(cell.value is None for cell in row[1:]):
            row[0].font = Font(name="Calibri", size=12, bold=True, color="425C50")
            ws.row_dimensions[row[0].row].height = 36


def _rows(wb, name, columns, limit):
    if name not in wb.sheetnames:
        return None
    ws = wb[name]
    if (ws.max_row or 0) > 1500 or (ws.max_column or 0) > 40:
        raise ValueError(f"{name} : feuille trop grande (1 000 lignes maximum).")
    header = [str(cell.value or "").strip() for cell in next(ws.iter_rows(min_row=1, max_row=1), [])]
    expected = {col[1]: col[0] for col in columns}
    if len([item for item in header if item]) != len({item for item in header if item}):
        raise ValueError(f"{name} : en-tête en double.")
    required = {"Name", "IEC type"} if name.startswith(("DUT_", "SIGNALS_")) else {"Table", "COM member", "Direction"}
    if not required.issubset(header):
        raise ValueError(f"{name} : en-têtes manquants : {', '.join(sorted(required - set(header)))}.")
    unknown = set(header) - set(expected) - {""}
    if unknown:
        raise ValueError(f"{name} : en-têtes inconnus : {', '.join(sorted(unknown))}.")
    rows = []
    for rownum, cells in enumerate(ws.iter_rows(min_row=2), 2):
        if rownum > 1500:
            raise ValueError(f"{name} : feuille trop grande (1 000 lignes maximum).")
        if all(cell.value in (None, "") for cell in cells):
            continue
        row = {}
        for col, cell in zip(header, cells):
            if cell.data_type == "f":
                raise ValueError(f"{name}!{cell.coordinate} : saisis une valeur, pas une formule Excel.")
            if cell.value is not None and len(str(cell.value)) > 1000:
                raise ValueError(f"{name}!{cell.coordinate} : valeur trop longue.")
            if col in expected:
                row[expected[col]] = cell.value if cell.value is not None else ""
        row["_row"] = rownum
        rows.append(row)
    if len(rows) > limit:
        raise ValueError(f"{name} : maximum {limit} lignes.")
    return rows


def _text(value):
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip() if value is not None else ""


def _message(error):
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, item['loc']))} : {item['msg']}" for item in error.errors()[:4])
    return str(error)


def _combined_type(state, draft):
    declarations, _, _ = _declared(Variables(signals=draft.signals), state)
    result = copy.deepcopy(draft.duts)
    by_role = {dut["role"]: dut for dut in result}
    for role, member in declarations.values():
        if role not in by_role:
            by_role[role] = {"role": role, "members": []}
            result.append(by_role[role])
        by_role[role]["members"].append(member)
    return {"name": draft.name, "duts": result}


def import_draft(data, state, current):
    if len(data) > MAX_BYTES:
        raise ValueError("Le classeur dépasse 8 Mo.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 500 or sum(info.file_size for info in archive.infolist()) > 24 * 1024 * 1024:
                raise ValueError("Classeur trop volumineux une fois décompressé.")
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Fichier Excel .xlsx invalide ou illisible.") from exc
    try:
        return _import_workbook(wb, state, current)
    finally:
        wb.close()


def _import_workbook(wb, state, current):
    draft = current.model_copy(deep=True)
    if draft.kind not in ROLES:
        raise ValueError("Catégorie de type inconnue.")
    if "TYPE" in wb.sheetnames:
        meta = dict(wb["TYPE"].iter_rows(min_row=2, max_row=5, max_col=2, values_only=True))
        if meta.get("format") != FORMAT:
            raise ValueError("Format de classeur inconnu. Télécharge le modèle depuis le menu Excel de création de type.")
        if meta.get("kind") and meta["kind"] != draft.kind:
            raise ValueError(f"Ce classeur concerne {meta['kind']}. Choisis d’abord cette catégorie dans l’assistant.")
        if meta.get("generic_template_id") and meta["generic_template_id"] != draft.generic_template_id:
            raise ValueError("Choisis le même objet générique que celui du classeur avant de l’importer.")
    known_types = {dut["name"] for typ in state.get("types", []) for dut in typ.get("duts", [])}
    known_types.update(f"DUT_{role}_{draft.name}" for role in ROLES[draft.kind] if _identifier(draft.name))
    replaced = []
    for protocol in ("io", "tcp", "rtu"):
        sheet = "SIGNALS_" + protocol.upper()
        rows = _rows(wb, sheet, SIGNAL_COLUMNS, 100)
        if rows is None:
            continue
        if draft.kind != "Equipment" and rows:
            raise ValueError(f"{sheet} : les signaux sont réservés aux équipements.")
        parsed = []
        for row in rows:
            location = f"{sheet} ligne {row.pop('_row')}"
            try:
                row = {key: _text(value) for key, value in row.items()}
                row["id"] = row.get("id") or str(uuid4())
                row["transport"] = protocol
                row["iec_type"] = row.get("iec_type", "").upper()
                parsed.append(Signal.model_validate(row).model_dump())
                _declared(Variables(signals=[parsed[-1]]), state)
            except (ValueError, ValidationError) as exc:
                raise ValueError(f"{location} : {_message(exc)}") from exc
        draft.signals = [signal for signal in draft.signals if signal.get("transport", "io") != protocol] + parsed
        replaced.append(sheet)
    by_role = {dut["role"]: dut for dut in draft.duts}
    for role in ("PA", "COM", "EXC", "IO", "INT"):
        sheet = "DUT_" + role
        rows = _rows(wb, sheet, MEMBER_COLUMNS, 1000)
        if rows is None:
            continue
        if role not in ROLES[draft.kind] and rows:
            raise ValueError(f"{sheet} : ce DUT n’appartient pas à cette catégorie de type.")
        parsed = []
        for row in rows:
            location = f"{sheet} ligne {row.pop('_row')}"
            prefix, name = _text(row.pop("prefix", "")), _text(row.get("name"))
            if not name or prefix and not _identifier(prefix):
                raise ValueError(f"{location} : nom et préfixe IEC valides requis.")
            row["name"] = f"{prefix}_{name}" if prefix else name
            if not _identifier(row["name"]):
                raise ValueError(f"{location} : nom de membre IEC invalide.")
            error = _iec_type_error(row.get("iec_type"), known_types)
            if error:
                raise ValueError(f"{location} : {error}")
            row = {key: _text(value) for key, value in row.items()}
            row["id"] = row.get("id") or str(uuid4())
            parsed.append(row)
        by_role[role] = {"role": role, "members": parsed}
        replaced.append(sheet)
    draft.duts = [by_role.get(role, {"role": role, "members": []}) for role in ROLES[draft.kind]]
    if len(draft.signals) > 100:
        raise ValueError("Maximum 100 signaux au total.")
    typ = _combined_type(state, draft)
    if draft.generic_template_id:
        validation_state = {**state, "types": [*state.get("types", []), {"duts": [{"name": name} for name in known_types]}]}
        try:
            _declared(Variables(signals=draft.signals, members=[
                {**member, "role": dut["role"]} for dut in draft.duts for member in dut["members"]]), validation_state)
        except (ValueError, ValidationError) as exc:
            raise ValueError(f"Variables du modèle hérité : {_message(exc)}") from exc
    for dut in typ["duts"]:
        names = [_text(member.get("name")).casefold() for member in dut.get("members", [])]
        if len(names) != len(set(names)):
            raise ValueError(f"DUT_{dut['role']} : nom en double, éventuellement déjà déclaré dans SIGNALS. Garde une seule déclaration par variable.")
    for protocol in ("tcp", "rtu"):
        sheet = "MODBUS_" + protocol.upper()
        rows = _rows(wb, sheet, MAPPING_COLUMNS, 1000)
        if rows is None:
            continue
        parsed = []
        for row in rows:
            location = f"{sheet} ligne {row.pop('_row')}"
            direction = _text(row.get("inbound")).casefold()
            if direction not in {"inbound", "outbound"}:
                raise ValueError(f"{location} : Direction doit être inbound ou outbound.")
            row["inbound"] = direction == "inbound"
            raw = row.get("register")
            try:
                if raw not in (None, ""):
                    row["register"] = int(str(raw), 16) if str(raw).lower().startswith("0x") else int(str(raw))
                else:
                    row["register"] = None
                for field in ("bank", "bank_type", "function", "channel"):
                    row[field] = _text(row.get(field)) or None
                row["protocol"] = protocol
                row["order"] = row.get("order") or "LSW_FIRST"
                parsed.append(Mapping.model_validate(row).model_dump(by_alias=True))
            except (ValueError, ValidationError) as exc:
                raise ValueError(f"{location} : registre ou mappage invalide ({_message(exc)}).") from exc
        draft.modbus_template = [row for row in draft.modbus_template if row.get("protocol") != protocol] + parsed
        replaced.append(sheet)
    if not replaced:
        raise ValueError("Aucune feuille de variables reconnue. Télécharge le modèle depuis le menu Excel de création de type.")
    if len(draft.modbus_template) > 1000:
        raise ValueError("Maximum 1 000 lignes de mappage Modbus au total.")
    try:
        validate_rows(state, typ, [Mapping.model_validate(row) for row in draft.modbus_template])
    except ValueError as exc:
        raise ValueError(f"MODBUS_TCP / MODBUS_RTU : {exc}") from exc
    if draft.generic_template_id and sum(len(dut["members"]) for dut in draft.duts) > 150:
        raise ValueError("Maximum 150 variables supplémentaires pour un objet hérité.")
    return {"draft": draft.model_dump(), "sheets": replaced,
            "summary": {"signals": len(draft.signals), "members": sum(len(dut["members"]) for dut in draft.duts),
                        "mappings": len(draft.modbus_template)}}


def register_type_excel_routes(app, get_session, current_user, csrf):
    @app.post("/api/v2/types/draft/excel")
    def download(draft: Draft, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "Projet indisponible.")
        try:
            data = export_draft(state, draft)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        name = re.sub(r"[^A-Za-z0-9_-]", "_", draft.name) or "TEMPLATE"
        return Response(data, media_type=MIME, headers={"Content-Disposition": f'attachment; filename="TYPE_{name}_variables.xlsx"', "Cache-Control": "no-store"})

    @app.post("/api/v2/types/draft/excel/import")
    def upload(file: UploadFile = File(...), current: str = Form(...),
                     session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        try:
            data = file.file.read(MAX_BYTES + 1)
        finally:
            file.file.close()
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "Projet indisponible.")
        try:
            return import_draft(data, state, Draft.model_validate(json.loads(current)))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, _message(exc)) from exc
