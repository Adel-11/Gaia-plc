"""Excel editing of unsaved type variables; importing never persists a project."""
from __future__ import annotations

import copy
import io
import json
import re
import unicodedata
import zipfile
from typing import Literal
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
SCOPE_LABELS = {"all": "Variables du type", "signals": "Signaux", "duts": "Blocs / DUT", "modbus": "Modbus"}
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
    ("name", "Name", "Signal name or label. Spaces, accents and punctuation are converted to an IEC identifier on import; the original label is retained as a comment.", None),
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
    ("member", "Member", "Member of the selected COM or HMI domain, without its prefix. A union REAL uses Member.Real_value.", "COM_Members"),
    ("inbound", "Direction", "inbound = client/equipment to PLC. outbound = PLC to client/equipment.", "Mapping_Directions"),
    ("register", "RTU register", "Manufacturer register, zero based, decimal or 0x hexadecimal. TCP addresses are assigned on instances.", None),
    ("order", "Word order", "LSW_FIRST = low word first. MSW_FIRST = high word first. REAL and 32-bit formats use two registers.", "Word_Orders"),
    ("bank", "PLC bank", "Optional existing RTU array slot, e.g. Device_Registers[0]. Usually leave blank.", None),
    ("bank_type", "PLC bank type", "Only when preserving an existing RTU bank, e.g. ARRAY[0..9] OF WORD.", None),
    ("function", "Function", "Optional existing RTU function code. Usually leave blank.", None),
    ("channel", "Channel", "Optional existing RTU channel label. Usually leave blank.", None),
    ("role", "Domain", "COM = field equipment. HMI = supervision. Both use the same Modbus address pool.", "Domains"),
]


def _mapping_columns(protocol):
    return MAPPING_COLUMNS if protocol == "rtu" else [column for column in MAPPING_COLUMNS
                                                      if column[0] in {"table", "member", "inbound", "order", "role"}]


class Draft(BaseModel):
    scope: Literal["all", "signals", "duts", "modbus"] = "all"
    name: str = Field(default="", max_length=128)
    kind: str = "Equipment"
    generic_template_id: str | None = None
    signals: list[dict] = Field(default_factory=list, max_length=100)
    duts: list[dict] = Field(default_factory=list, max_length=6)
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
        hmi_members = list(_members(_combined_type(state, draft), state, 'HMI'))
    except (ValueError, ValidationError, KeyError):
        members = [str(m.get("name", "")) for dut in draft.duts if dut.get("role") == "COM" for m in dut.get("members", []) if m.get("name")]
        hmi_members = [str(m.get('name', '')) for dut in draft.duts if dut.get('role') == 'HMI' for m in dut.get('members', []) if m.get('name')]
    return {
        "IEC_Types": list(dict.fromkeys(types)), "Signal_IO": sorted(ANALOG_TYPES),
        "Signal_Modbus": sorted(MODBUS_TYPES), "Signal_Digital": ["BOOL"],
        "Directions": ["input", "output"], "Signal_Kinds": ["analog", "digital"],
        "Prefixes": list(dict.fromkeys(PREFIXES + options.get("prefixes", []))),
        "Sections": list(dict.fromkeys(SECTIONS + options.get("sections", []))),
        "Tables": ["coils", "discrete_inputs", "input_registers", "holding_registers"],
        "Mapping_Directions": ["inbound", "outbound"], "Word_Orders": ["LSW_FIRST", "MSW_FIRST"],
        "COM_Members": members or [""],
        "HMI_Members": hmi_members or [''], "Domains": ['COM', 'HMI'],
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
        if name == "MODBUS_RTU" and columns[col - 1][0] in {"bank", "bank_type", "function", "channel"}:
            ws.column_dimensions[letter].hidden = not any(row.get(columns[col - 1][0]) not in (None, "") for row in rows)
        if choice and choice != 'COM_Members':
            if choice == "Signal_Types":
                family = "Signal_IO" if transport == "io" else "Signal_Modbus"
                formula = f'IF($C2="digital",Signal_Digital,{family})'
            else:
                formula = choice
            custom = choice in {"IEC_Types", "Sections", "Prefixes"}
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
    for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1, max_col=len(columns)):
        ws.row_dimensions[row[0].row].height = 34
        for cell in row:
            cell.font = Font(name="Calibri", size=11, color="000000")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(2, len(rows) + 1)}"
    return ws


def _split_member(member, prefixes):
    name = str(member.get("name") or "")
    prefix = next((p for p in sorted(prefixes, key=len, reverse=True) if name.startswith(p + "_")), "")
    return {**member, "prefix": prefix, "name": name[len(prefix) + 1:] if prefix else name}


def _selected_sheets(draft):
    signals = {"SIGNALS_IO", "SIGNALS_TCP", "SIGNALS_RTU"}
    duts = {"DUT_" + role for role in ROLES[draft.kind]}
    modbus = {"MODBUS_TCP", "MODBUS_RTU"}
    if draft.scope == "signals" and draft.kind != "Equipment":
        raise ValueError("L’étape Signaux est réservée aux équipements.")
    return {"signals": signals, "duts": duts, "modbus": modbus,
            "all": (signals if draft.kind == "Equipment" else set()) | duts | modbus}[draft.scope]


def _guide(wb, draft, selected):
    columns = [("sheet", "Sheet", "Worksheet to fill.", None), ("column", "Column", "Column to fill.", None),
               ("purpose", "Function and purpose", "How this field is used.", None),
               ("rules", "Rules", "Accepted values and validation rules.", None),
               ("example", "Example", "Fictitious value, for illustration only.", None)]
    rows = [
        {"sheet": SCOPE_LABELS[draft.scope], "column": "Utilisation", "purpose": "Remplis les feuilles de saisie de ce fichier, puis importe-le depuis le menu Excel de cette même étape.",
         "rules": "L’import remplit les formulaires modifiables. GUIDE, EXAMPLES, VARIABLES et TYPE ne créent aucune variable. Le code ST et les autres étapes sont conservés."},
        {"column": "Remplacement", "purpose": "Une feuille présente remplace sa partie de la saisie. Une feuille vide la vide. Une feuille absente laisse cette partie inchangée.",
         "rules": "Vérifie les données à l’écran avant de créer le type. Annuler l’import restaure la saisie précédente de cette étape."},
        {"column": "Exemples", "purpose": "EXAMPLES contient des variables factices. Copie uniquement les lignes voulues sous les en-têtes de la feuille de saisie correspondante.",
         "rules": "Les exemples ne sont jamais importés automatiquement. En Modbus, remplace les membres COM factices par ceux que tu as déclarés aux étapes précédentes."},
        {"column": "Héritage", "purpose": "Les colonnes ID masquées permettent de renommer une variable héritée en conservant son identité.",
         "rules": "Conserve les ID existants. Pour ajouter une variable, laisse ID vide. Sélectionne le même objet générique dans l’assistant."},
    ]
    rules = {
        "prefix": ("Facultatif. Choisir un préfixe ou en saisir un valide. Le séparateur _ est ajouté automatiquement.", "INPUT + PRESSURE → INPUT_PRESSURE"),
        "name": ("Obligatoire. Lettres, chiffres et _. Ne commence pas par un chiffre. Nom unique dans son DUT, sans mot réservé IEC.", "PRESSURE"),
        "iec_type": ("Obligatoire. Utilise la liste compatible avec le contexte. Un DUT connu, ARRAY[0..9] OF WORD et STRING(80) peuvent être saisis dans les DUT.", "REAL / BOOL / TON / ARRAY[0..9] OF WORD"),
        "section": ("Facultatif. Regroupe les déclarations sous un commentaire dans le DUT. Ce n’est pas une section VAR_INPUT ou VAR_OUTPUT.", "INPUT (field to plc)"),
        "default_value": ("Facultatif. Littéral ST compatible avec le type ; ne pas saisir de formule Excel. Zéro et FALSE sont conservés.", "0 / FALSE / T#5s / 'Pump'"),
        "unit": ("Facultatif. Unité affichée en documentation ; ne convertit pas la mesure.", "V / A / Hz / bar"),
        "comment": ("Facultatif. Texte de documentation repris avec la variable.", "Tension entre les phases L1 et L2"),
        "id": ("Identifiant technique masqué. Conserver pour un renommage ; laisser vide pour une nouvelle variable.", "Ne pas modifier un ID existant"),
        "direction": ("Obligatoire : input = terrain vers PLC ; output = PLC vers terrain. Indépendant du rôle client/serveur Modbus.", "input"),
        "kind": ("Obligatoire : digital impose BOOL ; analog propose les types numériques acceptés pour le protocole.", "analog / digital"),
        "io_name": ("Facultatif. Identifiant IEC strict, avec _ et sans espaces ni tirets. Vide = nom généré avec INPUT_/OUTPUT_ et le protocole pour COM.", "TCP_INPUT_L1_L2_VOLTAGE"),
        "internal_name": ("Facultatif. Identifiant IEC strict. Vide = nom du signal. Crée le membre correspondant du DUT INT.", "L1_L2_VOLTAGE"),
        "exchange_name": ("Facultatif, seulement pour une entrée. Identifiant IEC strict. Vide = OUTPUT_ suivi du nom du signal.", "OUTPUT_L1_L2_VOLTAGE"),
        "table": ("Obligatoire. coils/discrete_inputs pour BOOL ; input_registers/holding_registers pour les mots. Les tables en lecture seule imposent le sens.", "input_registers"),
        "member": ("Obligatoire. Copier la colonne Member de VARIABLES avec le Domain correspondant, sans COM. ou HMI. devant. Aucune liste de variables n’est imposée. Un REAL issu d’un signal Modbus utilise .Real_value.", "TCP_INPUT_VOLTAGE.Real_value"),
        "inbound": ("Obligatoire. inbound = client TCP ou équipement RTU vers PLC ; outbound = PLC vers client/équipement. TCP : DI/IR outbound. RTU : DI/IR inbound.", "inbound / outbound"),
        "register": ("RTU : obligatoire, entier de 0 à 65535, base zéro, décimal ou hexadécimal 0x. Pas de chevauchement ; un REAL occupe deux registres. TCP : laisser vide.", "20 / 0x0014"),
        "order": ("LSW_FIRST = mot faible en premier ; MSW_FIRST = mot fort en premier. Pour les formats de 32 bits, dont REAL, concerne les deux registres.", "MSW_FIRST"),
        "bank": ("Option avancée RTU, normalement vide. Pour conserver un tableau PLC existant, préciser un élément et aussi son type dans PLC bank type.", "Device_Registers[0]"),
        "bank_type": ("Seulement si PLC bank est renseigné. Tableau commençant à 0, de WORD, BOOL ou BYTE ; taille compatible avec l’index et le format.", "ARRAY[0..9] OF WORD"),
        "function": ("Option avancée d’un canal RTU existant. Laisser vide pour une nouvelle configuration.", "3"),
        "channel": ("Libellé facultatif d’un canal RTU existant. Laisser vide pour une nouvelle configuration.", "Read_measurements"),
        "role": ("COM pour les équipements de terrain, HMI pour les IHM / SCADA. Choisir ensuite un membre de ce DUT. Les plages d’adresses sont communes.", "COM / HMI"),
    }
    families = [("SIGNALS_", SIGNAL_COLUMNS), ("DUT_", MEMBER_COLUMNS), ("MODBUS_", MAPPING_COLUMNS)]
    for prefix, fields in families:
        sheets = sorted(name for name in selected if name.startswith(prefix))
        if not sheets:
            continue
        for field, title, description, _ in fields:
            field_sheets = [name for name in sheets if not name.startswith("MODBUS_") or
                            field in {column[0] for column in _mapping_columns(name[7:].lower())}]
            if not field_sheets:
                continue
            rule, example = rules[field]
            if prefix == "SIGNALS_" and field == "name":
                rule = "Obligatoire. Un libellé comme L1-L2 Voltage est accepté : espaces, tirets et accents sont convertis en nom IEC. La conversion est affichée à l’import et le libellé est conservé en commentaire. Deux noms convertis identiques sont refusés."
                example = "L1-L2 Voltage → L1_L2_Voltage"
            if prefix == "SIGNALS_" and field == "iec_type":
                rule = "digital : BOOL. analog IO : BYTE, WORD, DWORD, INT, UINT, DINT, UDINT, REAL, LREAL. analog TCP/RTU : WORD, INT, UINT, REAL. Un REAL Modbus crée un membre COM union avec .Real_value."
            rows.append({"sheet": ", ".join(field_sheets), "column": title, "purpose": description,
                         "rules": rule, "example": example})
    guide = _sheet(wb, "GUIDE", columns, rows)
    for letter, width in zip("ABCDE", (28, 24, 58, 85, 43)):
        guide.column_dimensions[letter].width = width
    for index in range(2, len(rows) + 2):
        guide.row_dimensions[index].height = 84


def export_draft(state, draft, *, empty=False):
    if draft.kind not in ROLES:
        raise ValueError("Catégorie de type inconnue.")
    wb = Workbook()
    wb.remove(wb.active)
    choices = _choices(state, draft)
    selected = _selected_sheets(draft)
    _guide(wb, draft, selected)
    _sheet(wb, "TYPE", [("key", "Field", "Context only; select identity and inheritance in the browser.", None), ("value", "Value", "Context only.", None)], [
        {"key": "format", "value": FORMAT}, {"key": "name", "value": draft.name},
        {"key": "kind", "value": draft.kind}, {"key": "generic_template_id", "value": draft.generic_template_id or ""},
        {"key": "scope", "value": draft.scope},
    ])
    for protocol in ("io", "tcp", "rtu"):
        if "SIGNALS_" + protocol.upper() in selected:
            _sheet(wb, "SIGNALS_" + protocol.upper(), SIGNAL_COLUMNS,
                   [] if empty else [row for row in draft.signals if row.get("transport", "io") == protocol], transport=protocol)
    for role in ROLES[draft.kind]:
        if "DUT_" + role not in selected:
            continue
        members = next((dut.get("members", []) for dut in draft.duts if dut.get("role") == role), [])
        _sheet(wb, "DUT_" + role, MEMBER_COLUMNS, [] if empty else [_split_member(row, choices["Prefixes"]) for row in members])
    for protocol in ("tcp", "rtu"):
        if "MODBUS_" + protocol.upper() not in selected:
            continue
        _sheet(wb, "MODBUS_" + protocol.upper(), _mapping_columns(protocol),
               [] if empty else [{**row, "inbound": "inbound" if row.get("inbound") else "outbound"}
                for row in draft.modbus_template if row.get("protocol") == protocol])
    _examples(wb, choices, selected)
    if any(name.startswith('MODBUS_') for name in selected):
        from backend.v2_modbus_templates import _members
        try:
            combined = _combined_type(state, draft)
        except (ValueError, ValidationError):
            # A template can be downloaded while another wizard field is incomplete.
            combined = {'name': draft.name, 'duts': draft.duts}
        _sheet(wb, 'VARIABLES', [
            ('member', 'Member', 'Copy this value into the mapping Member column.', None),
            ('role', 'Domain', 'Copy the matching COM or HMI domain.', None),
            ('iec_type', 'IEC type', 'Type used to select a compatible Modbus table.', None),
            ('path', 'FB path', 'Qualified path within the FB.', None),
        ], [{'member': member, 'role': role, 'iec_type': kind, 'path': role + '.' + member}
            for role in ('COM', 'HMI') for member, kind in _members(combined, state, role).items()])
    lists = wb.create_sheet("_Choices")
    for col, (name, values) in enumerate(choices.items(), 1):
        lists.cell(1, col, name)
        for row, value in enumerate(values, 2):
            _cell(lists.cell(row, col), value)
        letter = get_column_letter(col)
        wb.defined_names.add(DefinedName(name, attr_text=f"'_Choices'!${letter}$2:${letter}${max(2, len(values) + 1)}"))
    lists.sheet_state = "hidden"
    wb["TYPE"].sheet_state = "hidden"
    order = [name for name in wb.sheetnames if name in selected] + (['VARIABLES'] if 'VARIABLES' in wb.sheetnames else []) + ["EXAMPLES", "GUIDE", "TYPE", "_Choices"]
    for index, name in enumerate(order):
        wb.move_sheet(name, offset=index - wb.sheetnames.index(name))
    wb.active = next(index for index, name in enumerate(wb.sheetnames) if name in selected)
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    from backend.v2_excel_plain import plain_xlsx
    return plain_xlsx(output.getvalue())


def _examples(wb, choices, selected):
    ws = wb.create_sheet("EXAMPLES")
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "C4AB73"
    ws.append(["Exemples uniquement. Copie les lignes utiles vers la feuille indiquée. Cette feuille n’est jamais importée."])
    ws.merge_cells("A1:J1")
    def section(title, columns, rows):
        if title not in selected and not (title.startswith("DUT members:") and "DUT_INT" in selected):
            return
        ws.append([])
        ws.append([title])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=len(columns))
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
    for role in ("PA", "COM", "HMI", "EXC", "IO", "INT"):
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
                         "order": "LSW_FIRST" if i % 2 == 0 else "MSW_FIRST", 'role': 'COM'})
        section("MODBUS_" + protocol.upper(), _mapping_columns(protocol), rows)
    ws.freeze_panes = "B2"
    for col in range(1, 11):
        ws.column_dimensions[get_column_letter(col)].width = 28
    ws.column_dimensions["F"].width = 60
    for row in ws:
        ws.row_dimensions[row[0].row].height = 34
        for cell in row:
            cell.font = Font(name="Calibri", size=12, color="263F32")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if row[0].value and all(cell.value is None for cell in row[1:]):
            row[0].font = Font(name="Calibri", size=12, bold=True, color="425C50")
            ws.row_dimensions[row[0].row].height = 36


def _rows(wb, name, columns, limit, *, required=None, max_cell_length=1000):
    if name not in wb.sheetnames:
        return None
    ws = wb[name]
    max_rows = max(1500, limit + 1)
    if (ws.max_row or 0) > max_rows or (ws.max_column or 0) > 40:
        raise ValueError(f"{name} : feuille trop grande ({limit} lignes maximum).")
    header = [str(cell.value or "").strip() for cell in next(ws.iter_rows(min_row=1, max_row=1), [])]
    if name.startswith('MODBUS_'):
        header = ['Member' if item == 'COM member' else item for item in header]
    expected = {col[1]: col[0] for col in columns}
    if len([item for item in header if item]) != len({item for item in header if item}):
        raise ValueError(f"{name} : en-tête en double.")
    if required is None:
        required = {"Name", "IEC type"} if name.startswith(("DUT_", "SIGNALS_")) else {"Table", "Member", "Direction"}
    if not required.issubset(header):
        raise ValueError(f"{name} : en-têtes manquants : {', '.join(sorted(required - set(header)))}.")
    unknown = set(header) - set(expected) - {""}
    if unknown:
        raise ValueError(f"{name} : en-têtes inconnus : {', '.join(sorted(unknown))}.")
    rows = []
    for rownum, cells in enumerate(ws.iter_rows(min_row=2), 2):
        if rownum > max_rows:
            raise ValueError(f"{name} : feuille trop grande ({limit} lignes maximum).")
        if all(cell.value in (None, "") for cell in cells):
            continue
        row = {}
        for col, cell in zip(header, cells):
            if cell.data_type == "f":
                raise ValueError(f"{name}!{cell.coordinate} : saisis une valeur, pas une formule Excel.")
            if cell.value is not None and len(str(cell.value)) > max_cell_length:
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


def _normalize_signal_name(row, location, warnings):
    original = row.get("name", "")
    if not original or _identifier(original):
        return
    ascii_name = unicodedata.normalize("NFKD", original).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9_]+", "_", ascii_name).strip("_")
    if not name:
        raise ValueError(f"{location} : indique un nom contenant des lettres ou des chiffres.")
    if not _identifier(name):
        name = "SIGNAL_" + name
    row["name"] = name
    row["comment"] = row.get("comment") or original
    warnings.append(f"{location} : « {original} » → « {name} ».")


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


def read_workbook(data):
    if len(data) > MAX_BYTES:
        raise ValueError("Le classeur dépasse 8 Mo.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 500 or sum(info.file_size for info in archive.infolist()) > 24 * 1024 * 1024:
                raise ValueError("Classeur trop volumineux une fois décompressé.")
        return load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Fichier Excel .xlsx invalide ou illisible.") from exc
def import_draft(data, state, current):
    wb = read_workbook(data)
    try:
        return _import_workbook(wb, state, current)
    finally:
        wb.close()


def _import_workbook(wb, state, current):
    draft = current.model_copy(deep=True)
    if draft.kind not in ROLES:
        raise ValueError("Catégorie de type inconnue.")
    selected = _selected_sheets(draft)
    if "TYPE" in wb.sheetnames:
        meta = dict(wb["TYPE"].iter_rows(min_row=2, max_row=6, max_col=2, values_only=True))
        if meta.get("format") != FORMAT:
            raise ValueError("Format de classeur inconnu. Télécharge le modèle depuis le menu Excel de création de type.")
        if meta.get("kind") and meta["kind"] != draft.kind:
            raise ValueError(f"Ce classeur concerne {meta['kind']}. Choisis d’abord cette catégorie dans l’assistant.")
        if meta.get("generic_template_id") and meta["generic_template_id"] != draft.generic_template_id:
            raise ValueError("Choisis le même objet générique que celui du classeur avant de l’importer.")
        file_scope = meta.get("scope") or "all"
        if file_scope not in SCOPE_LABELS:
            raise ValueError("Étape Excel inconnue. Télécharge le modèle de l’étape courante.")
        if draft.scope != "all" and file_scope not in {"all", draft.scope}:
            raise ValueError(f"Ce classeur concerne l’étape {SCOPE_LABELS[file_scope]}. Ouvre le menu Excel de cette étape pour l’importer.")
    known_types = {dut["name"] for typ in state.get("types", []) for dut in typ.get("duts", [])}
    known_types.update(f"DUT_{role}_{draft.name}" for role in ROLES[draft.kind] if _identifier(draft.name))
    replaced, warnings = [], []
    for protocol in ("io", "tcp", "rtu"):
        sheet = "SIGNALS_" + protocol.upper()
        if sheet not in selected:
            continue
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
                _normalize_signal_name(row, location, warnings)
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
    for role in ("PA", "COM", "HMI", "EXC", "IO", "INT"):
        sheet = "DUT_" + role
        if sheet not in selected:
            continue
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
    if draft.scope in {"all", "duts"}:
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
        if sheet not in selected:
            continue
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
                row['role'] = _text(row.get('role')) or 'COM'
                row["order"] = row.get("order") or "LSW_FIRST"
                parsed.append(Mapping.model_validate(row).model_dump(by_alias=True))
            except (ValueError, ValidationError) as exc:
                raise ValueError(f"{location} : registre ou mappage invalide ({_message(exc)}).") from exc
        draft.modbus_template = [row for row in draft.modbus_template if row.get("protocol") != protocol] + parsed
        replaced.append(sheet)
    if not replaced:
        raise ValueError(f"Aucune feuille de variables reconnue pour l’étape {SCOPE_LABELS[draft.scope]}. Télécharge le modèle de cette étape.")
    if len(draft.modbus_template) > 1000:
        raise ValueError("Maximum 1 000 lignes de mappage Modbus au total.")
    if draft.scope in {"all", "modbus"}:
        try:
            validate_rows(state, typ, [Mapping.model_validate(row) for row in draft.modbus_template])
        except ValueError as exc:
            raise ValueError(f"MODBUS_TCP / MODBUS_RTU : {exc}") from exc
    if draft.generic_template_id and sum(len(dut["members"]) for dut in draft.duts) > 150:
        raise ValueError("Maximum 150 variables supplémentaires pour un objet hérité.")
    return {"draft": draft.model_dump(), "sheets": replaced, "warnings": warnings,
            "summary": {"signals": len(draft.signals), "members": sum(len(dut["members"]) for dut in draft.duts),
                        "mappings": len(draft.modbus_template)}}


def register_type_excel_routes(app, get_session, current_user, csrf):
    @app.post("/api/v2/types/draft/excel")
    def download(draft: Draft, empty: bool = False, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "Projet indisponible.")
        try:
            data = export_draft(state, draft, empty=empty)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        name = re.sub(r"[^A-Za-z0-9_-]", "_", draft.name) or "TEMPLATE"
        suffix = "variables" if draft.scope == "all" else draft.scope
        return Response(data, media_type=MIME, headers={"Content-Disposition": f'attachment; filename="TYPE_{name}_{suffix}.xlsx"', "Cache-Control": "no-store"})

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
