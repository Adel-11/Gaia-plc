"""Edit one FB mapping section through Excel, without changing saved project state."""
from io import BytesIO
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field, ValidationError

from backend.v2_excel_plain import plain_xlsx
from backend.v2_fb_flow import validate_mappings
from backend.v2_type_excel import MAX_BYTES, MIME, _message, _rows, _sheet, read_workbook

FORMAT = 'gaia-fb-mapping/1'
COLUMNS = [
    ('source', 'Source', 'Copy a source path from VARIABLES, or combine BOOL paths with AND, OR and parentheses.', None),
    ('destination', 'Destination', 'Copy a single destination path from VARIABLES. Its IEC type must match the source.', None),
    ('id', 'ID', 'Keep this hidden identity for existing mappings. Leave blank for a new mapping.', None),
]


class Variable(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    iec_type: str = Field(min_length=1, max_length=128)


class Mapping(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    section: Literal['inputs', 'outputs']
    source: str = Field(default='', max_length=4096)
    destination: str = Field(default='', max_length=300)


class Draft(BaseModel):
    name: str = Field(default='FB', max_length=128)
    section: Literal['inputs', 'outputs']
    mappings: list[Mapping] = Field(default_factory=list, max_length=2000)
    variables: list[Variable] = Field(default_factory=list, max_length=10000)


def _usage(section, path):
    family = path.split('.')[0]
    if section == 'inputs':
        return family in {'PA', 'COM', 'HMI', 'EXC', 'IO'}, family == 'INTERN'
    return family == 'INTERN', family in {'COM', 'HMI', 'EXC', 'IO'}


def export_mappings(draft, empty=False):
    wb = Workbook()
    wb.remove(wb.active)
    sheet = draft.section.upper()
    _sheet(wb, sheet, COLUMNS, [] if empty else [row.model_dump() for row in draft.mappings if row.section == draft.section])
    variables = []
    for variable in draft.variables:
        if variable.iec_type.upper().startswith(('ARRAY', 'DUT_', 'FB_')):
            continue
        source, destination = _usage(draft.section, variable.path)
        if source or destination:
            variables.append({'path': variable.path, 'type': variable.iec_type, 'family': variable.path.split('.')[0],
                              'source': 'yes' if source else '', 'destination': 'yes' if destination else ''})
    _sheet(wb, 'VARIABLES', [
        ('path', 'Path', 'Copy this exact text into Source or Destination. No variable dropdown is required.', None),
        ('type', 'IEC type', 'Source and destination must have the same IEC type.', None),
        ('family', 'Family', 'DUT role. INT members are referenced with INTERN.', None),
        ('source', 'Source allowed', 'yes means this variable can be a source in this section.', None),
        ('destination', 'Destination allowed', 'yes means this variable can be a destination in this section.', None),
    ], variables)
    examples = ([{'source': 'IO.INPUT_READY', 'destination': 'INTERN.READY'},
                 {'source': '(IO.INPUT_READY AND HMI.INPUT_START) OR EXC.INPUT_OVERRIDE', 'destination': 'INTERN.RUN'},
                 {'source': 'COM.TCP_INPUT_PRESSURE.Real_value', 'destination': 'INTERN.PRESSURE'}]
                if draft.section == 'inputs' else [
                 {'source': 'INTERN.RUN', 'destination': 'IO.OUTPUT_RUN'},
                 {'source': 'INTERN.PRESSURE', 'destination': 'HMI.OUTPUT_PRESSURE'},
                 {'source': 'INTERN.SETPOINT', 'destination': 'COM.TCP_OUTPUT_SETPOINT.Real_value'}])
    _sheet(wb, 'EXAMPLES', COLUMNS, examples)
    _sheet(wb, 'GUIDE', [('column', 'Column', '', None), ('purpose', 'Function and purpose', '', None),
                        ('rules', 'Rules', '', None), ('example', 'Example', '', None)], [
        {'column': sheet, 'purpose': f'Mapping des {"entrées" if draft.section == "inputs" else "sorties"} du FB {draft.name}.',
         'rules': 'Une ligne = une recopie. L’import remplace cette section dans le formulaire, pas l’autre section. Enregistre ensuite le FB pour appliquer le ST.'},
        {'column': 'Source', 'purpose': 'Variable lue, à copier depuis VARIABLES dans la colonne Source.',
         'rules': 'Entrées : PA/COM/HMI/EXC/IO. Sorties : INTERN. Pour BOOL, AND / OR et parenthèses sont acceptés. Ne pas saisir := ni de point-virgule.', 'example': examples[0]['source']},
        {'column': 'Destination', 'purpose': 'Variable écrite, à copier depuis VARIABLES dans la colonne Destination.',
         'rules': 'Entrées : INTERN. Sorties : COM/HMI/EXC/IO. Une seule variable, du même type IEC que la source. Destination unique dans cette section.', 'example': examples[0]['destination']},
        {'column': 'ID', 'purpose': 'Identité technique masquée des mappings existants.', 'rules': 'Conserver pour modifier une ligne exportée. Laisser vide pour une nouvelle ligne.'},
        {'column': 'VARIABLES', 'purpose': 'Toutes les variables mappables du FB et leurs types.', 'rules': 'Copie la colonne Path. Les colonnes Source allowed et Destination allowed indiquent où coller le chemin. Les membres HMI sont distincts des membres COM terrain.'},
        {'column': 'EXAMPLES', 'purpose': 'Variables factices, pour montrer les recopies et les expressions BOOL.', 'rules': 'Remplacer les chemins par ceux de VARIABLES. Cette feuille et GUIDE ne sont jamais importées.'},
        {'column': 'Suppression', 'purpose': 'Une ligne retirée du fichier sera retirée de cette section après import.', 'rules': 'Une feuille vide vide le mapping de cette section. Aucun code PROCESSING ni aucune déclaration de variable ne sont modifiés.'},
    ])
    for letter, width in zip('ABCD', (24, 65, 85, 45)):
        wb['GUIDE'].column_dimensions[letter].width = width
    for row in range(2, 9):
        wb['GUIDE'].row_dimensions[row].height = 68
    meta = wb.create_sheet('_CONTEXT')
    meta.append(['Format', FORMAT]); meta.append(['Section', draft.section]); meta.append(['FB', draft.name])
    meta.sheet_state = 'hidden'
    stream = BytesIO()
    wb.save(stream)
    wb.close()
    return plain_xlsx(stream.getvalue())


def import_mappings(data, draft):
    wb = read_workbook(data)
    try:
        if '_CONTEXT' in wb.sheetnames:
            meta = dict(wb['_CONTEXT'].iter_rows(max_row=3, max_col=2, values_only=True))
            if meta.get('Format') != FORMAT or meta.get('Section') != draft.section:
                raise ValueError('Ce fichier ne correspond pas à cette section. Utilise le menu Excel des entrées ou des sorties concernées.')
        sheet = draft.section.upper()
        rows = _rows(wb, sheet, COLUMNS, 2000, required={'Source', 'Destination'}, max_cell_length=4096)
        if rows is None:
            raise ValueError(f'Feuille {sheet} introuvable. Télécharge le modèle de cette section.')
        allowed = {variable.path: re.sub(r'\s+', '', variable.iec_type).upper() for variable in draft.variables}
        saved = {(row.id, row.source, row.destination) for row in draft.mappings if row.section == draft.section}
        mappings = []
        for row in rows:
            line = row.pop('_row')
            candidate = Mapping(id=str(row.get('id') or uuid4()), section=draft.section,
                                source=str(row.get('source') or '').strip(), destination=str(row.get('destination') or '').strip()).model_dump()
            errors = validate_mappings([candidate], allowed, saved)
            if errors:
                raise ValueError(f'{sheet} ligne {line} : {errors[0]}')
            mappings.append(candidate)
        errors = validate_mappings(mappings, allowed, saved)
        if errors:
            raise ValueError(f'{sheet} : {errors[0]}')
        return {'mappings': mappings, 'section': draft.section, 'count': len(mappings)}
    finally:
        wb.close()


def register_fb_excel_routes(app, current_user, csrf):
    @app.post('/api/v2/fb-mappings/excel')
    def download(draft: Draft, empty: bool = False, user=Depends(current_user), _=Depends(csrf)):
        name = re.sub(r'[^A-Za-z0-9_-]', '_', draft.name) or 'FB'
        return Response(export_mappings(draft, empty), media_type=MIME, headers={
            'Content-Disposition': f'attachment; filename="{name}_{draft.section}.xlsx"', 'Cache-Control': 'no-store'})

    @app.post('/api/v2/fb-mappings/excel/import')
    def upload(file: UploadFile = File(...), current: str = Form(...), user=Depends(current_user), _=Depends(csrf)):
        try:
            data = file.file.read(MAX_BYTES + 1)
        finally:
            file.file.close()
        try:
            return import_mappings(data, Draft.model_validate(json.loads(current)))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, _message(exc)) from exc
