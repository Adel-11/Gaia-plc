import io

import openpyxl
import pytest

from backend.v2_engine import load_pack
from backend.v2_type_excel import Draft, export_draft, import_draft


@pytest.fixture(scope="module")
def state():
    return load_pack()


def workbook_bytes(book):
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def filled_draft():
    return Draft(name="QA_EXCEL_SENSOR", signals=[
        {"id": "measure", "name": "MEASURE", "direction": "input", "kind": "analog", "iec_type": "DWORD", "transport": "io"},
        {"id": "command", "name": "COMMAND", "direction": "output", "kind": "digital", "iec_type": "BOOL", "transport": "tcp"},
        {"id": "temperature", "name": "TEMPERATURE", "direction": "input", "kind": "analog", "iec_type": "REAL", "transport": "rtu"},
    ], duts=[
        {"role": "PA", "members": [{"name": "ENABLED", "iec_type": "BOOL", "default_value": False},
                                   {"name": "OFFSET", "iec_type": "INT", "default_value": 0}]},
        {"role": "INT", "members": [{"name": "DELAY", "iec_type": "TON"},
                                    {"name": "BUFFER", "iec_type": "ARRAY[0..3] OF WORD"},
                                    {"name": "LABEL", "iec_type": "STRING(80)", "default_value": "'Sensor'"}]},
    ], modbus_template=[
        {"protocol": "tcp", "table": "coils", "member": "TCP_OUTPUT_COMMAND", "inbound": True, "order": "LSW_FIRST"},
        {"protocol": "rtu", "table": "input_registers", "member": "RTU_INPUT_TEMPERATURE.Real_value", "inbound": True, "register": 20, "order": "MSW_FIRST"},
    ])


def test_empty_template_has_examples_supported_types_and_real_excel_dropdowns(state):
    data = export_draft(state, Draft())
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert {"SIGNALS_IO", "SIGNALS_TCP", "SIGNALS_RTU", "DUT_PA", "DUT_COM", "DUT_EXC", "DUT_IO", "DUT_INT", "MODBUS_TCP", "MODBUS_RTU", "EXAMPLES"} <= set(wb.sheetnames)
    assert wb["_Choices"].sheet_state == "hidden"
    assert len(wb["EXAMPLES"]["A"]) > 60
    assert wb["DUT_COM"]["C1"].comment and wb["DUT_COM"].freeze_panes == "C2"
    assert {"BOOL", "REAL", "TON", "DUT_Union_Real_Word"} <= {row[0] for row in wb["_Choices"].iter_rows(min_row=2, values_only=True)}
    validations = list(wb["SIGNALS_RTU"].data_validations.dataValidation)
    assert any('Signal_Modbus' in validation.formula1 and 'Signal_Digital' in validation.formula1 for validation in validations)
    assert all(not validation.showDropDown for validation in validations)
    assert import_draft(data, state, Draft())["summary"] == {"signals": 0, "members": 0, "mappings": 0}


def test_excel_roundtrip_fills_a_real_type_preview_without_persisting(tmp_path, monkeypatch, state):
    from tests.test_v2_type_creation import _client

    client, headers = _client(tmp_path, monkeypatch)
    try:
        before = client.get('/api/v2/project').json()
        draft = filled_draft()
        assert client.post('/api/v2/types/draft/excel', json=draft.model_dump()).status_code == 403
        response = client.post('/api/v2/types/draft/excel', json=draft.model_dump(), headers=headers)
        assert response.status_code == 200 and 'TYPE_QA_EXCEL_SENSOR_variables.xlsx' in response.headers['content-disposition']
        assert client.post('/api/v2/types/draft/excel/import', files={'file': ('type.xlsx', response.content)}, data={'current': draft.model_dump_json()}).status_code == 403
        imported = client.post('/api/v2/types/draft/excel/import', headers=headers,
                               files={'file': ('type.xlsx', response.content)}, data={'current': draft.model_dump_json()})
        assert imported.status_code == 200, imported.text
        result = imported.json()
        assert result['summary'] == {'signals': 3, 'members': 5, 'mappings': 2}
        members = result['draft']['duts'][0]['members']
        assert members[0]['default_value'] == 'FALSE' and members[1]['default_value'] == '0'
        assert result['draft']['modbus_template'][1]['order'] == 'MSW_FIRST'
        scoped = draft.model_copy(update={'scope': 'signals'})
        template = client.post('/api/v2/types/draft/excel?empty=true', headers=headers, json=scoped.model_dump())
        assert template.status_code == 200 and '_signals.xlsx' in template.headers['content-disposition']
        step_result = client.post('/api/v2/types/draft/excel/import', headers=headers,
                                  files={'file': ('signals.xlsx', template.content)}, data={'current': scoped.model_dump_json()})
        assert step_result.status_code == 200, step_result.text
        assert step_result.json()['draft']['signals'] == []
        assert step_result.json()['draft']['duts'] == draft.duts
        assert step_result.json()['draft']['modbus_template'] == draft.modbus_template
        preview = client.post('/api/v2/types/preview', headers=headers, json={
            'revision': before['revision'], 'name': draft.name, 'kind': draft.kind,
            'signals': result['draft']['signals'], 'duts': result['draft']['duts'],
            'modbus_template': result['draft']['modbus_template'], 'processing_code': '// Kept outside the workbook',
        }).json()
        assert preview.get('token') and not preview['errors'], preview
        after = client.get('/api/v2/project').json()
        assert before['revision'] == after['revision'] and before['types'] == after['types']
        saved = client.post('/api/v2/types/apply', headers=headers, json={'token': preview['token']})
        assert saved.status_code == 200, saved.text
        created = saved.json()['type']
        assert len(created['modbus_template']) == 2
        com = next(dut for dut in created['duts'] if dut['role'] == 'COM')
        assert {'TCP_OUTPUT_COMMAND', 'RTU_INPUT_TEMPERATURE'} <= {member['name'] for member in com['members']}
    finally:
        client.close()


def test_partial_workbook_replaces_only_present_sheets_and_preserves_row_order(state):
    current = filled_draft()
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, current)))
    for name in list(wb.sheetnames):
        if name != 'DUT_PA':
            del wb[name]
    wb['DUT_PA'].delete_rows(2, 30)
    wb['DUT_PA'].append(['', 'SECOND', 'REAL', 'PARAMETERS', 0, 'bar', 'First row'])
    wb['DUT_PA'].append(['', 'FIRST', 'INT', 'PARAMETERS', 12, '', 'Second row'])
    result = import_draft(workbook_bytes(wb), state, current)
    assert result['sheets'] == ['DUT_PA']
    assert [row['name'] for row in result['draft']['duts'][0]['members']] == ['SECOND', 'FIRST']
    assert result['draft']['signals'] == current.signals
    assert result['draft']['modbus_template'] == current.modbus_template
    wb['DUT_PA'].delete_rows(2, 30)
    assert import_draft(workbook_bytes(wb), state, current)['draft']['duts'][0]['members'] == []


@pytest.mark.parametrize('sheet,cell,value,message', [
    ('DUT_PA', 'C2', 'NOT_A_REAL_TYPE', 'DUT_PA ligne 2'),
    ('SIGNALS_IO', 'D2', 'BOOL', 'SIGNALS_IO ligne 2'),
    ('MODBUS_RTU', 'D2', 65535, 'registre RTU'),
    ('MODBUS_TCP', 'B2', 'MISSING_VARIABLE', 'MISSING_VARIABLE'),
    ('DUT_PA', 'E2', '=1+1', 'DUT_PA!E2'),
])
def test_invalid_import_is_atomic_and_points_to_the_problem(state, sheet, cell, value, message):
    current = filled_draft()
    original = current.model_dump()
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, current)))
    wb[sheet][cell] = value
    with pytest.raises(ValueError, match=message):
        import_draft(workbook_bytes(wb), state, current)
    assert current.model_dump() == original


def test_prefixes_duplicates_and_generic_identities(state):
    current = Draft(name='QA_INHERITED', generic_template_id='sensor', signals=[
        {'id': 'measure', 'name': 'OLD', 'direction': 'input', 'kind': 'analog', 'iec_type': 'DWORD'}])
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, current)))
    wb['SIGNALS_IO']['A2'] = 'NEW'
    wb['DUT_COM'].append(['HMI', 'COMMAND', 'BOOL', 'INPUT (field to plc)', False])
    result = import_draft(workbook_bytes(wb), state, current)['draft']
    assert result['signals'][0]['id'] == 'measure' and result['signals'][0]['name'] == 'NEW'
    assert next(d for d in result['duts'] if d['role'] == 'COM')['members'][0]['name'] == 'HMI_COMMAND'
    wb['DUT_IO'].append(['INPUT', 'NEW', 'DWORD'])
    with pytest.raises(ValueError, match='en double'):
        import_draft(workbook_bytes(wb), state, current)
    with pytest.raises(ValueError, match='même objet générique'):
        import_draft(export_draft(state, current), state, Draft())


def test_unknown_files_and_upi_workbook(state):
    with pytest.raises(ValueError, match='invalide'):
        import_draft(b'not an xlsx', state, Draft())
    wb = openpyxl.Workbook()
    with pytest.raises(ValueError, match='Aucune feuille'):
        import_draft(workbook_bytes(wb), state, Draft())
    draft = Draft(kind='Functional unit (UPI)')
    data = export_draft(state, draft)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert 'DUT_PA' not in wb.sheetnames and 'SIGNALS_IO' not in wb.sheetnames
    assert import_draft(data, state, draft)['summary']['members'] == 0


def test_inherited_member_renaming_keeps_processing_links(state):
    from backend.v2_generic_objects import STARTERS, Inheritance, materialize

    template = next(template for template in STARTERS if template['id'] == 'hmi-local')
    draft = Draft(name='QA_HMI_EXCEL', generic_template_id=template['id'], duts=[
        {'role': role, 'members': [member for member in template['members'] if member['role'] == role]}
        for role in ('PA', 'COM', 'HMI', 'EXC', 'IO', 'INT')])
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, draft)))
    # MODE_LOCAL has no prefix and keeps its hidden source identity.
    wb['DUT_INT']['B2'] = 'LOCAL_MODE'
    result = import_draft(workbook_bytes(wb), state, draft)['draft']
    inheritance = Inheritance(template_id=template['id'], signals=result['signals'], members=[
        {**member, 'role': dut['role']} for dut in result['duts'] for member in dut['members']])
    _, body = materialize(state, inheritance)
    assert 'INTERN.LOCAL_MODE' in body and 'INTERN.MODE_LOCAL' not in body


def test_all_documented_examples_can_be_imported_together(state):
    draft = Draft(name='QA_ALL_EXAMPLES')
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, draft)))
    target = None
    for values in wb['EXAMPLES'].iter_rows(values_only=True):
        if all(value is None for value in values):
            target = None
        elif values[0] in wb.sheetnames:
            target = values[0]
        elif str(values[0]).startswith('DUT members:'):
            target = 'DUT_INT'
        elif target and values[0] not in {'Name', 'Prefix', 'Table'}:
            wb[target].append(values)
    imported = import_draft(workbook_bytes(wb), state, draft)
    assert imported['summary']['signals'] == 12
    assert imported['summary']['mappings'] == 8
    assert imported['summary']['members'] > 25


@pytest.mark.parametrize('scope,prefix', [('signals', 'SIGNALS_'), ('duts', 'DUT_'), ('modbus', 'MODBUS_')])
def test_step_templates_only_include_the_step_and_document_every_column(state, scope, prefix):
    draft = filled_draft().model_copy(update={'scope': scope})
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, draft, empty=True)))
    data_sheets = [sheet for sheet in wb if sheet.title not in {'TYPE', '_Choices', 'GUIDE', 'EXAMPLES', 'VARIABLES'}]
    assert data_sheets and all(sheet.title.startswith(prefix) for sheet in data_sheets)
    assert wb['TYPE'].sheet_state == 'hidden'
    visible = [sheet.title for sheet in wb if sheet.sheet_state == 'visible']
    assert visible[-2:] == ['EXAMPLES', 'GUIDE']
    guide = list(wb['GUIDE'].iter_rows(min_row=2, values_only=True))
    for sheet in data_sheets:
        assert not any(any(value is not None for value in row) for row in sheet.iter_rows(min_row=2, values_only=True))
        for cell in sheet[1]:
            if cell.value:
                assert any(sheet.title in str(row[0]) and row[1] == cell.value and row[2] and row[3] and row[4] for row in guide)
    if scope == 'modbus':
        assert [cell.value for cell in wb['MODBUS_TCP'][1]] == ['Table', 'Member', 'Direction', 'Word order', 'Domain']
        choices = list(wb['_Choices'].iter_rows(values_only=True))
        column = choices[0].index('COM_Members')
        assert 'TCP_OUTPUT_COMMAND' in [row[column] for row in choices[1:]]


@pytest.mark.parametrize('scope,changed', [('signals', 'signals'), ('duts', 'duts'), ('modbus', 'modbus_template')])
def test_step_import_preserves_every_other_step_even_from_legacy_full_file(state, scope, changed):
    current = filled_draft().model_copy(update={'scope': scope})
    empty_full = export_draft(state, Draft(name=current.name))
    result = import_draft(empty_full, state, current)['draft']
    for field in ('signals', 'duts', 'modbus_template'):
        if field != changed:
            assert result[field] == getattr(current, field)
    if changed == 'duts':
        assert not any(dut['members'] for dut in result[changed])
    else:
        assert result[changed] == []
    source = export_draft(state, Draft(scope='signals'))
    if scope != 'signals':
        with pytest.raises(ValueError, match='étape Signaux'):
            import_draft(source, state, current)


def test_signal_labels_are_normalized_visibly_and_collisions_are_atomic(state):
    current = Draft(scope='signals')
    wb = openpyxl.load_workbook(io.BytesIO(export_draft(state, current)))
    wb['SIGNALS_TCP'].append(['L1-L2 Voltage', 'input', 'analog', 'REAL', 'V'])
    result = import_draft(workbook_bytes(wb), state, current)
    signal = result['draft']['signals'][0]
    assert signal['name'] == 'L1_L2_Voltage' and signal['comment'] == 'L1-L2 Voltage'
    assert len(result['warnings']) == 1 and 'L1_L2_Voltage' in result['warnings'][0]
    wb['SIGNALS_TCP'].append(['L1 L2 Voltage', 'input', 'analog', 'REAL', 'V'])
    with pytest.raises(ValueError, match='en double'):
        import_draft(workbook_bytes(wb), state, current)
    assert current.signals == []
