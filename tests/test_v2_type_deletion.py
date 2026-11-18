import copy

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend import v2_store as store, v2_plc as plc
from backend.v2_engine import load_pack
from backend.v2_type_deletion import project_blockers


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'delete-author')
    monkeypatch.setenv('ADMIN_PASSWORD', 'delete-author-password')
    app = create_app(data_dir=tmp_path, secret_key='delete-test-secret-' * 3, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post('/api/login', json={'username': 'delete-author', 'password': 'delete-author-password'}).json()
        yield app, client, {'X-CSRF-Token': auth['csrf_token']}


def create_type(client, headers):
    state = client.get('/api/v2/project').json()
    review = client.post('/api/v2/types/preview', headers=headers, json={
        'revision': state['revision'], 'name': 'UNUSED_TYPE', 'kind': 'Equipment'}).json()
    assert review['token'], review
    result = client.post('/api/v2/types/apply', headers=headers, json={'token': review['token']})
    assert result.status_code == 200, result.text
    return result.json()['type']


def delete_preview(client, headers, type_id):
    return client.post('/api/v2/types/delete/preview', headers=headers, json={
        'revision': client.get('/api/v2/project').json()['revision'], 'type_id': type_id})


def test_unused_type_review_delete_blocks_workbook_history_and_token_guards(workspace):
    app, client, headers = workspace
    typ = create_type(client, headers)
    before = client.get('/api/v2/project').json()
    payload = {'revision': before['revision'], 'type_id': typ['id']}
    assert client.post('/api/v2/types/delete/preview', json=payload).status_code == 403
    assert client.post('/api/v2/types/delete/preview', headers=headers, json={**payload, 'revision': -1}).status_code == 409
    assert delete_preview(client, headers, 'MISSING').status_code == 404
    review = delete_preview(client, headers, typ['id']).json()
    assert review['token'] and not review['errors'], review
    assert client.get('/api/v2/project').json()['revision'] == before['revision']
    assert any(typ['fb']['name'] in row for row in review['changes'])
    token = {'token': review['token']}
    assert client.post('/api/v2/types/delete/apply', json=token).status_code == 403
    # A token from another operation must never authorize a deletion.
    other = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': before['revision'], 'action': 'create', 'type_id': typ['id'], 'name': 'NOT_APPLIED'}).json()
    assert client.post('/api/v2/types/delete/apply', headers=headers, json={'token': other['token']}).status_code == 404
    result = client.post('/api/v2/types/delete/apply', headers=headers, json=token)
    assert result.status_code == 200, result.text
    assert client.post('/api/v2/types/delete/apply', headers=headers, json=token).status_code == 404
    after = client.get('/api/v2/project').json()
    assert not any(t['id'] == typ['id'] for t in after['types'])
    assert after['instances'] == before['instances']
    assert after['version'] != before['version']
    assert client.get('/api/v2/exports/' + typ['source_file']).status_code == 404
    with app.state.session_factory() as session:
        state = store.load_project(session)
        assert typ['source_file'] not in state['workbooks']
        objects = plc.build_program(state)
        assert typ['fb']['name'] not in objects
        assert all(d['name'] not in objects for d in typ['duts'])


def test_type_used_in_another_project_blocks_even_after_review_then_can_delete(workspace):
    _, client, headers = workspace
    typ = create_type(client, headers)
    first = client.get('/api/v2/project').json()
    prepared = delete_preview(client, headers, typ['id']).json()
    assert prepared['token']
    assert client.post('/api/v2/workspace/projects', headers=headers, json={'name': 'Other plant'}).status_code == 200
    second = client.get('/api/v2/project').json()
    review = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': second['revision'], 'action': 'create', 'name': 'PUMP_OTHER', 'type_id': typ['id']}).json()
    assert client.post('/api/v2/instances/apply', headers=headers, json={'token': review['token']}).status_code == 200
    # Wrong project, then an unchanged origin revision: both paths are protected.
    assert client.post('/api/v2/types/delete/apply', headers=headers, json={'token': prepared['token']}).status_code == 409
    client.post('/api/v2/workspace/select', headers=headers, json={'id': first['project_id']})
    blocked = delete_preview(client, headers, typ['id']).json()
    assert blocked['token'] is None
    assert any('Other plant' in b and 'PUMP_OTHER' in b for b in blocked['blockers'])
    raced = client.post('/api/v2/types/delete/apply', headers=headers, json={'token': prepared['token']})
    assert raced.status_code == 409 and 'PUMP_OTHER' in raced.text
    client.post('/api/v2/workspace/select', headers=headers, json={'id': second['project_id']})
    state = client.get('/api/v2/project').json()
    removal = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': state['revision'], 'action': 'delete', 'instance_id': 'PUMP_OTHER'}).json()
    assert removal['token'], removal
    assert client.post('/api/v2/instances/apply', headers=headers, json={'token': removal['token']}).status_code == 200
    final_review = delete_preview(client, headers, typ['id']).json()
    assert final_review['token'], final_review
    assert client.post('/api/v2/types/delete/apply', headers=headers, json={'token': final_review['token']}).status_code == 200
    client.post('/api/v2/workspace/select', headers=headers, json={'id': first['project_id']})
    assert all(t['id'] != typ['id'] for t in client.get('/api/v2/project').json()['types'])


def test_dependencies_include_other_dut_and_authored_code_but_ignore_comments():
    state = copy.deepcopy(load_pack())
    typ = {'id': 'DETACH', 'name': 'DETACH', 'duts': [{'name': 'DUT_INT_DETACH', 'role': 'INT', 'members': [{'name': 'SPARE', 'iec_type': 'BOOL'}]}],
           'fb': {'name': 'FB_INST_DETACH', 'body': ''}}
    state['types'].append(typ)
    state['types'][0]['duts'][0]['members'].append({'name': 'REFERENCE', 'iec_type': 'DUT_INT_DETACH'})
    state.setdefault('generated_custom_code', {})['PLC_PRG'] = '// FB_INST_DETACH\n(* DUT_INT_DETACH *)\n'
    assert any(state['types'][0]['duts'][0]['name'] in b for b in project_blockers(state, typ))
    state['types'][0]['duts'][0]['members'].pop()
    assert project_blockers(state, typ) == []
    state['generated_custom_code']['PLC_PRG'] += 'FB_INST_DETACH();\n'
    assert any('PLC_PRG' in b for b in project_blockers(state, typ))


def test_existing_pack_type_reports_instances_then_exchange_signatures():
    state = copy.deepcopy(load_pack())
    typ = next(t for t in state['types'] if t['name'] == 'ADM100W')
    state['types'] = [t for t in state['types'] if t['id'] != typ['id']]
    assert project_blockers(state, typ) == ['Instance ADM100W_1']
    state['instances'] = [i for i in state['instances'] if i['type_id'] != typ['id']]
    assert any('FC_EXC_UPI_LOCAL_FUXA_ADM100W' in b for b in project_blockers(state, typ))


def test_delete_linked_instance_releases_rows_and_legacy_exchange_calls(workspace):
    from backend.v2_addressing import free_addresses

    app, client, headers = workspace
    with app.state.session_factory() as session:
        state = store.load_project(session)
        state['exchange_calls'].append({'id': 'legacy', 'FC': state['exchange_functions'][0]['name'],
                                       'Instance A': 'DIP1500_1', 'Instance B': 'UPI_LOCAL_HMI'})
        store.save_project(session, state, 'delete-author', state['revision'])
        session.commit()
    before = client.get('/api/v2/project').json()
    released_point = next(row for row in before['tcp_points'] if row.get('Instance') == 'DIP1500_1')
    assert free_addresses(before, released_point['Client'], released_point['table'], 1) != [released_point['address']]
    review = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': before['revision'], 'action': 'delete', 'instance_id': 'DIP1500_1'}).json()
    assert review['token'], review
    assert client.post('/api/v2/instances/apply', headers=headers, json={'token': review['token']}).status_code == 200
    state = client.get('/api/v2/project').json()
    assert state['types'] == before['types']
    assert not any(i['name'] == 'DIP1500_1' for i in state['instances'])
    assert all('DIP1500_1' not in names for names in state['program_membership'].values())
    for key in ('tcp_points', 'rtu_channels', 'rtu_slaves', 'pa_values'):
        assert not any((r.get('instance_id') or r.get('instance') or r.get('Instance')) == 'DIP1500_1' for r in state[key])
    assert not any('DIP1500_1' in {r.get('instance_a_id'), r.get('instance_b_id'), r.get('Instance A'), r.get('Instance B')} for r in state['exchange_calls'])
    objects = plc.build_program(state)
    modbus = objects['GVL_MODBUS']['element']
    assert not modbus.xpath("./p:variable[starts-with(@name,'DIP1500_')]", namespaces={'p': plc.PLC})
    assert 'DIP1500_1' not in {row['name'] for row in state['instances']}
    assert free_addresses(state, released_point['Client'], released_point['table'], 1) == [released_point['address']]


def test_instance_deletion_cleans_iomap_rack_and_export(workspace):
    from backend.v2_io_copies import assign_copy, remove_copy, variable_rows
    from backend.v2_io_hardware import STATE_KEY
    from backend.v2_type_deletion import code_identifiers
    from tests.test_v2_io_hardware import RACK

    app, client, headers = workspace
    with app.state.session_factory() as session:
        state = store.load_project(session)
        for _, row, key in list(variable_rows(state)):
            if row.get('GVL_IO member') == 'GVL_IO.IO_DOSENSOR_1.INPUT_SIGNAL':
                remove_copy(state, key)
        assign_copy(state, {'name': 'QA_SENSOR_ANALOG', 'iec_type': 'DWORD', 'direction': 'input'},
                    'DOSENSOR_1', 'INPUT_SIGNAL', 'input')
        state[STATE_KEY] = {'filename': 'rack.xml', 'application': 'Application', 'xml': RACK.decode()}
        store.save_project(session, state, 'delete-author', state['revision'])
        session.commit()
    before = client.get('/api/v2/project').json()
    deleted_iomap = {str(row.get('GVL_IOMAP variable')).split('.')[-1].casefold()
                     for key in ('io_channels', 'virtual_io') for row in before.get(key, [])
                     if (row.get('Instance') or row.get('instance_id')) == 'DOSENSOR_1'}
    review = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': before['revision'], 'action': 'delete', 'instance_id': 'DOSENSOR_1'}).json()
    assert review.get('token'), review
    # Preview does not mutate the live project.
    assert any(row['name'] == 'DOSENSOR_1' for row in client.get('/api/v2/project').json()['instances'])
    assert client.post('/api/v2/instances/apply', headers=headers, json={'token': review['token']}).status_code == 200
    with app.state.session_factory() as session:
        state = store.load_project(session)
    assert state['types'] == before['types']
    assert not any(row.get('Instance') == 'DOSENSOR_1' for row in state['virtual_io'])
    assert 'Application.GVL_IOMAP.IOMAP_DI_FAULT_DOSENSOR_1' not in state[STATE_KEY]['xml']
    assert all('DOSENSOR_1' not in members for members in state['program_membership'].values())
    objects = plc.build_program(state)
    for name, obj in objects.items():
        source = '\n'.join(obj['element'].xpath(
            './/*[local-name()="ST" or local-name()="InterfaceAsPlainText"]/*[local-name()="xhtml"]/text()'))
        assert not {'com_dosensor_1', 'io_dosensor_1', 'pa_dosensor_1', 'exc_dosensor_1', 'fb_dosensor_1'} & code_identifiers(source), name
    gvl = objects['GVL_IOMAP']['element']
    assert not deleted_iomap & {row.get('name').casefold() for row in gvl.findall(plc.NS + 'variable')}
    export = client.post('/api/v2/plc/preview', headers=headers, json={'mode': 'full'}).json()
    assert export.get('token') and not export['errors'], export


def test_custom_code_reference_blocks_instance_deletion_atomically(workspace):
    app, client, headers = workspace
    with app.state.session_factory() as session:
        state = store.load_project(session)
        state.setdefault('generated_custom_code', {})['PLC_PRG'] = 'GVL_COM.COM_DIP1500_1.HMI_INPUT_LOCAL_REQUEST := TRUE;'
        store.save_project(session, state, 'delete-author', state['revision'])
        session.commit()
    before = client.get('/api/v2/project').json()
    review = client.post('/api/v2/instances/preview', headers=headers, json={
        'revision': before['revision'], 'action': 'delete', 'instance_id': 'DIP1500_1'}).json()
    assert not review['token'] and any('PLC_PRG' in message for message in review['errors'])
    after = client.get('/api/v2/project').json()
    assert after['revision'] == before['revision']
    assert after['instances'] == before['instances'] and after['tcp_points'] == before['tcp_points']


@pytest.mark.parametrize('name', ['UPI_LOCAL_FUXA', 'UPI_LOCAL_HMI', 'UPI_LOCAL_WEBVISU'])
def test_upi_deletion_keeps_exchange_function_parameters(name):
    from backend.v2_instance_deletion import delete_instance
    from backend.v2_workspace import _compatibility_errors

    state = copy.deepcopy(load_pack())
    before = set(_compatibility_errors(state))
    functions = copy.deepcopy(state['exchange_functions'])
    delete_instance(state, next(instance for instance in state['instances'] if instance['name'] == name))
    assert state['exchange_functions'] == functions
    assert not any(name in {call.get('instance_a_id'), call.get('instance_b_id')} for call in state['exchange_calls'])
    assert not set(_compatibility_errors(state)) - before
