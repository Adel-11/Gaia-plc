import copy
from fastapi.testclient import TestClient
from backend.v2_engine import load_pack
from backend.v2_fb_flow import convert_body, render_flow, flow_view, update_flow, convert_project
from backend.v2_plc import build_program, serialize_program
from backend.v2_plc_validation import validate_xml
from backend.main import create_app


def test_auto_conversion_moves_legacy_section_code_once_and_is_idempotent():
    state = copy.deepcopy(load_pack())
    original = {t['name']: t['fb']['body'] for t in state['types']}
    assert convert_project(state)
    assert not convert_project(state)
    for typ in state['types']:
        expected = convert_body(original[typ['name']])
        assert typ['fb']['flow']['processing_code'] == expected['processing_code']
        assert render_flow(typ['fb']['flow']) == typ['fb']['body']
        assert flow_view(typ['fb'])['mappings']
        assert update_flow(typ, state, mappings=flow_view(typ['fb'])['mappings']) == []
        if expected.get('legacy_migrated'):
            assert typ['fb']['body'] != original[typ['name']]
            assert typ['fb']['flow'].get('legacy_migrated')
        else:
            assert typ['fb']['body'] == original[typ['name']]
    program = build_program(state)
    assert validate_xml(serialize_program(program, state, list(program)), state, program)['errors'] == []


def test_legacy_conditions_comments_and_calculations_move_into_processing_once():
    source = '// INPUTS\n(*\nINTERN.A := COM.A;\n*)\nIF COM.EN THEN\n INTERN.A := COM.A;\nEND_IF\nINTERN.A := COM.A + 1;\nINTERN.B := COM.B;\n// PROCESSING\nINTERN.A := 3;\n// OUTPUTS\nCOM.B := INTERN.B;\nINTERN.D := INTERN.C + 1;\n'
    flow = convert_body(source)
    body = render_flow(flow)
    fb = {'body': source, 'flow': flow}
    assert len(flow_view(fb)['mappings']) == 2
    view = flow_view(fb)
    assert 'preserved_sections' not in view
    assert 'IF COM.EN' in view['processing_code']
    assert 'INTERN.A := COM.A + 1;' in view['processing_code']
    assert 'INTERN.D := INTERN.C + 1;' in view['processing_code']
    assert view['processing_code'].count('INTERN.A := COM.A + 1;') == 1
    assert body.count('INTERN.A := COM.A + 1;') == 1
    assert view['migrations']


def test_mapping_and_processing_edits_reach_generated_xml_without_touching_custom_code(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','flow-admin');monkeypatch.setenv('ADMIN_PASSWORD','flow-password')
    app=create_app(data_dir=tmp_path,secret_key='flow-tests-'*4,cookie_secure=False)
    with TestClient(app) as client:
        auth=client.post('/api/login',json={'username':'flow-admin','password':'flow-password'}).json()
        headers={'X-CSRF-Token':auth['csrf_token']}
        current=client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()
        wires=copy.deepcopy(current['fb']['mappings'])
        output=next(w for w in wires if w['section']=='outputs' and w['destination']=='COM.SCADA_OUTPUT_LOCAL_ACTIVE')
        output['source']='INTERN.AUTO_REQUEST'
        response=client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview',headers=headers,json={
            'revision':current['revision'],'mappings':wires,'processing_code':'INTERN.LOCAL_ACTIVE := FALSE;\n','custom_code':'// custom stays exactly\r\n'})
        assert response.status_code==200,response.text
        assert not response.json()['errors'],response.json()
        assert client.post('/api/v2/objects/apply',headers=headers,json={'token':response.json()['token']}).status_code==200
        saved=client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()
        assert 'COM.SCADA_OUTPUT_LOCAL_ACTIVE := INTERN.AUTO_REQUEST;' in saved['fb']['body']
        assert saved['fb']['processing_code']=='INTERN.LOCAL_ACTIVE := FALSE;\n'
        assert saved['fb']['custom_code']=='// custom stays exactly\r\n'
        rejected=client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview',headers=headers,json={
            'revision':saved['revision'],'mappings':[{'section':'inputs','source':'COM.SCADA_OUTPUT_LOCAL_ACTIVE','destination':'COM.SCADA_INPUT_LOCAL_REQUEST'}]}).json()
        assert rejected['errors'] and not rejected['token']
        plan=client.post('/api/v2/plc/preview',headers=headers,json={'mode':'full'}).json()
        assert plan['errors']==[]
        assert b'COM.SCADA_OUTPUT_LOCAL_ACTIVE := INTERN.AUTO_REQUEST;' in client.get('/api/v2/plc/download/'+plan['token']).content
