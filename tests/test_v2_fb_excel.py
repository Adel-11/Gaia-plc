import copy
from io import BytesIO

import openpyxl
import pytest

from backend.v2_fb_excel import Draft, export_mappings, import_mappings
from backend.v2_fb_flow import update_flow


def draft(section='inputs'):
    return Draft(name='FB_Pump', section=section, variables=[
        {'path': path, 'iec_type': kind} for path, kind in [
            ('IO.READY', 'BOOL'), ('HMI.START', 'BOOL'), ('COM.VALUE', 'REAL'),
            ('INTERN.RUN', 'BOOL'), ('INTERN.VALUE', 'REAL'), ('IO.RUN', 'BOOL'),
            ('PA.ENABLED', 'BOOL'), ('COM.STRUCT', 'DUT_Test')]], mappings=[
        {'id': 'in', 'section': 'inputs', 'source': 'IO.READY', 'destination': 'INTERN.RUN'},
        {'id': 'out', 'section': 'outputs', 'source': 'INTERN.RUN', 'destination': 'IO.RUN'}])


def edited(data, edit):
    wb = openpyxl.load_workbook(BytesIO(data))
    edit(wb)
    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


@pytest.mark.parametrize('section', ['inputs', 'outputs'])
def test_section_roundtrip_catalogue_and_empty_template(section):
    current = draft(section)
    data = export_mappings(current)
    wb = openpyxl.load_workbook(BytesIO(data))
    assert wb.sheetnames == [section.upper(), 'VARIABLES', 'EXAMPLES', 'GUIDE', '_CONTEXT']
    assert not wb[section.upper()].data_validations.dataValidation
    assert wb[section.upper()]['A1'].fill.patternType == 'solid'
    assert wb[section.upper()]['A2'].fill.patternType is None
    paths = {row[0]: row for row in list(wb['VARIABLES'].values)[1:]}
    assert 'COM.STRUCT' not in paths
    assert paths['INTERN.RUN'][3:] == ((None, 'yes') if section == 'inputs' else ('yes', None))
    assert ('PA.ENABLED' in paths) == (section == 'inputs')
    result = import_mappings(data, current)
    assert result['mappings'] == [row.model_dump() for row in current.mappings if row.section == section]
    assert import_mappings(export_mappings(current, empty=True), current)['mappings'] == []
    with pytest.raises(ValueError, match='section'):
        import_mappings(data, draft('outputs' if section == 'inputs' else 'inputs'))


@pytest.mark.parametrize('source,destination,error', [
    ('IO.UNKNOWN', 'INTERN.RUN', 'Unknown mapping'),
    ('COM.VALUE AND IO.READY', 'INTERN.RUN', 'BOOL'),
    ('INTERN.RUN', 'IO.RUN', 'INPUTS'),
    ('IO.READY; DELETE()', 'INTERN.RUN', 'Invalid'),
])
def test_invalid_rows_have_location_and_do_not_mutate_draft(source, destination, error):
    current = draft()
    before = current.model_dump()
    data = edited(export_mappings(current), lambda wb: (
        setattr(wb['INPUTS']['A2'], 'value', source), setattr(wb['INPUTS']['B2'], 'value', destination)))
    with pytest.raises(ValueError, match='INPUTS ligne 2'):
        import_mappings(data, current)
    assert current.model_dump() == before


def test_copy_pasted_bool_expression_reaches_st_and_keeps_processing_and_outputs():
    current = draft()
    expression = '(IO.READY AND HMI.START) OR PA.ENABLED'
    data = edited(export_mappings(current), lambda wb: setattr(wb['INPUTS']['A2'], 'value', expression))
    result = import_mappings(data, current)
    typ = {'name': 'Pump', 'duts': [
        {'role': role, 'members': [{'name': name, 'iec_type': kind} for name, kind in members]}
        for role, members in [('IO', [('READY', 'BOOL'), ('RUN', 'BOOL')]), ('HMI', [('START', 'BOOL')]),
                              ('PA', [('ENABLED', 'BOOL')]), ('INT', [('RUN', 'BOOL')])]]}
    state = {'types': []}
    assert not update_flow(typ, state, mappings=[row.model_dump() for row in current.mappings],
                           processing_code='// Keep my logic\nINTERN.RUN := NOT INTERN.RUN;\n')
    before = copy.deepcopy(typ['fb']['flow'])
    from backend.v2_fb_flow import flow_view
    saved_output = next(row for row in flow_view(typ['fb'])['mappings'] if row['section'] == 'outputs')
    assert not update_flow(typ, state, mappings=result['mappings'] + [saved_output])
    assert f'INTERN.RUN := {expression};' in typ['fb']['body']
    assert typ['fb']['flow']['processing_code'] == before['processing_code']
    assert typ['fb']['flow']['output_segments'] == before['output_segments']


def test_duplicate_destinations_and_excel_formulas_are_rejected():
    current = draft()
    data = edited(export_mappings(current), lambda wb: wb['INPUTS'].append(['HMI.START', 'INTERN.RUN']))
    with pytest.raises(ValueError, match='Duplicate mapping destination'):
        import_mappings(data, current)
    data = edited(export_mappings(current), lambda wb: setattr(wb['INPUTS']['A2'], 'value', '=A3'))
    with pytest.raises(ValueError, match='formule Excel'):
        import_mappings(data, current)


def test_excel_endpoints_are_authenticated_csrf_protected_and_draft_only(tmp_path, monkeypatch):
    from tests.test_v2_type_creation import _client
    client, headers = _client(tmp_path, monkeypatch)
    try:
        current = draft()
        before = client.get('/api/v2/project').json()
        assert client.post('/api/v2/fb-mappings/excel', json=current.model_dump()).status_code == 403
        response = client.post('/api/v2/fb-mappings/excel', headers=headers, json=current.model_dump())
        assert response.status_code == 200
        assert 'FB_Pump_inputs.xlsx' in response.headers['content-disposition']
        args = {'files': {'file': ('mapping.xlsx', response.content)}, 'data': {'current': current.model_dump_json()}}
        assert client.post('/api/v2/fb-mappings/excel/import', **args).status_code == 403
        imported = client.post('/api/v2/fb-mappings/excel/import', headers=headers, **args)
        assert imported.status_code == 200, imported.text
        assert imported.json()['mappings'] == [current.mappings[0].model_dump()]
        invalid = client.post('/api/v2/fb-mappings/excel/import', headers=headers,
                              files={'file': ('bad.xlsx', b'bad')}, data={'current': current.model_dump_json()})
        assert invalid.status_code == 422
        assert client.get('/api/v2/project').json() == before
        client.post('/api/logout', headers=headers)
        assert client.post('/api/v2/fb-mappings/excel', json=current.model_dump()).status_code == 401
    finally:
        client.close()
