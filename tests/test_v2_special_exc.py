from copy import deepcopy

import pytest

from backend.v2_codesys import _st
from backend.v2_hmi import load_hmi_pack
from backend.v2_hmi_domain import migrate_state
from backend.v2_plc import NS, build_program, serialize_program
from backend.v2_plc_validation import _refs, validate_xml
from backend.v2_special_exc import KEY, options, render, replace


@pytest.fixture
def state():
    result = deepcopy(load_hmi_pack())
    migrate_state(result)
    return result


def instance(state, name='DIP1500_1'):
    return next(row for row in state['instances'] if row['name'] == name)


def row(state, **updates):
    member = next(item['member'] for item in options(state, instance(state)) if item['iec_type'] == 'BOOL')
    return {'id': 'qa-special', 'member': member, 'source': 'GVL_Machine.Ready AND PLC_PRG.Clock', 'comment': 'External readiness', **updates}


def errors(state, objects=None):
    objects = objects or build_program(deepcopy(state))
    return set(validate_xml(serialize_program(objects, state, list(objects)), state, objects)['errors'])


def test_external_assignment_is_generated_before_existing_calls_without_allocating_or_declaring(state):
    before = deepcopy(state)
    original_body = _st(build_program(deepcopy(state))['GFC_EXC']['element'])
    baseline = errors(state)
    replace(state, 'DIP1500_1', [row(state)])
    objects = build_program(deepcopy(state))
    body = _st(objects['GFC_EXC']['element'])
    assert body.startswith('// Affectation de variables particulières')
    assert body.endswith(original_body)
    target = f'GVL_EXC.EXC_DIP1500_1.{row(state)["member"]}'
    assert target + ' := GVL_Machine.Ready AND PLC_PRG.Clock;' in body
    assert 'GVL_Machine' not in objects and 'GVL_Machine' not in _refs(objects['GFC_EXC'])
    assert not errors(state, objects) - baseline
    for key in ('types', 'instances', 'tcp_points', 'rtu_channels', 'exchange_calls', 'program_membership'):
        assert state.get(key) == before.get(key)
    replace(state, 'DIP1500_1', [])
    assert not render(state)
    assert _st(build_program(deepcopy(state))['GFC_EXC']['element']) == original_body


def test_external_source_exception_never_hides_other_missing_references(state):
    baseline = errors(state)
    replace(state, 'DIP1500_1', [row(state)])
    objects = build_program(deepcopy(state))
    body = objects['GFC_EXC']['element'].find(NS + 'body/' + NS + 'ST/*')
    body.text += '\nGVL_Machine.Bad := TRUE;\n'
    assert 'GVL_Machine' in _refs(objects['GFC_EXC'])
    assert any('GVL_Machine' in error for error in errors(state, objects) - baseline)
    replace(state, 'DIP1500_1', [row(state, source='GVL_EXC.EXC_DIP1500_1.DoesNotExist')])
    assert any('DoesNotExist' in error for error in errors(state) - baseline)


@pytest.mark.parametrize('source', ['', 'A; B := TRUE;', 'A := B', 'GVL_A.Ready\nEND_IF', 'A // comment', '(A AND B', 'IF A THEN B'])
def test_source_rejects_statements_comments_and_unbalanced_expressions(state, source):
    with pytest.raises(ValueError):
        replace(state, 'DIP1500_1', [row(state, source=source)])
    assert not state.get(KEY)


def test_targets_duplicates_and_manual_gfc_are_checked_without_overwriting_code(state):
    with pytest.raises(ValueError, match='introuvable'):
        replace(state, 'DIP1500_1', [row(state, member='Unknown')])
    with pytest.raises(ValueError, match='double'):
        replace(state, 'DIP1500_1', [row(state), row(state, id='second')])
    state['st_overrides'] = {'GFC_EXC': {'body': 'GFC_EXC := FALSE;'}}
    with pytest.raises(ValueError, match='manuel'):
        replace(state, 'DIP1500_1', [row(state)])
    assert state['st_overrides']['GFC_EXC']['body'] == 'GFC_EXC := FALSE;'


def test_dut_rename_and_removal_update_special_targets_and_authored_sources(state):
    from backend.v2_member_sync import sync_consumers
    typ = next(item for item in state['types'] if item['name'] == 'DIP1500')
    original = row(state)['member']
    replace(state, 'DIP1500_1', [row(state)])
    other = instance(state, 'DOSENSOR_1')
    replace(state, other['name'], [{'member': options(state, other)[0]['member'],
                                   'source': f'GVL_EXC.EXC_DIP1500_1.{original}'}])
    member = next(member for dut in typ['duts'] if dut['role'] == 'EXC' for member in dut['members'] if member['name'] == original)
    member['name'] = 'QA_RENAMED'
    errors, changes = sync_consumers(state, typ, {'EXC': {original.casefold(): 'QA_RENAMED'}})
    assert not errors and changes
    assert state[KEY][0]['member'] == 'QA_RENAMED'
    assert state[KEY][1]['source'] == 'GVL_EXC.EXC_DIP1500_1.QA_RENAMED'
    typ['duts'] = [{**dut, 'members': [row for row in dut['members'] if row['name'] != 'QA_RENAMED']} if dut['role'] == 'EXC' else dut for dut in typ['duts']]
    sync_consumers(state, typ, {'EXC': {'qa_renamed': None}})
    assert state[KEY] == []


def test_instance_deletion_cleans_owned_and_cross_instance_sources(state):
    from backend.v2_instance_deletion import delete_instance
    replace(state, 'DIP1500_1', [row(state)])
    other = instance(state, 'DOSENSOR_1')
    replace(state, other['name'], [{'member': options(state, other)[0]['member'],
                                   'source': 'GVL_EXC.EXC_DIP1500_1.' + row(state)['member']}])
    delete_instance(state, instance(state))
    assert state[KEY] == []
    assert '// Affectation de variables particulières' not in _st(build_program(deepcopy(state))['GFC_EXC']['element'])


def test_reviewed_api_persists_per_instance_backup_and_keeps_focused_exports_working(tmp_path, monkeypatch):
    from tests.test_v2_type_creation import _client
    from backend import v2_store
    client, headers = _client(tmp_path, monkeypatch)
    try:
        with client.app.state.session_factory() as session:
            current = v2_store.load_project(session)
        before = client.get('/api/v2/project').json()
        assignment = row(current)
        body = {'revision': before['revision'], 'action': 'save_special', 'instance_a_id': 'DIP1500_1', 'assignments': [assignment]}
        assert client.post('/api/v2/exchanges/preview', json=body).status_code == 403
        preview = client.post('/api/v2/exchanges/preview', json=body, headers=headers).json()
        assert not preview['errors'] and preview['token'], preview
        assert '// Affectation de variables particulières' in preview['generated_st']
        assert client.get('/api/v2/project').json() == before
        assert client.post('/api/v2/exchanges/apply', json={'token': preview['token']}, headers=headers).status_code == 200
        saved = client.get('/api/v2/project').json()
        assert saved[KEY][0]['instance_id'] == 'DIP1500_1'
        assert saved['types'] == before['types']
        backup = client.post('/api/v2/workspace/backup', headers=headers).json()
        assert backup['state'][KEY] == saved[KEY]
        type_preview = client.post('/api/v2/types/preview', headers=headers, json={
            'revision': client.get('/api/v2/project').json()['revision'], 'name': 'QA_WITH_SPECIAL', 'kind': 'Equipment', 'duts': [], 'signals': []}).json()
        assert type_preview.get('token') and not type_preview['errors'], type_preview
        object_data = client.get('/api/v2/objects/type/DIP1500').json()
        object_preview = client.post('/api/v2/objects/type/DIP1500/preview', headers=headers, json={
            'revision': object_data['revision'], 'processing_code': object_data['fb']['processing_code']}).json()
        assert object_preview.get('token') and not object_preview['errors'], object_preview
        for path in ('/api/v2/plc/export/type/DIP1500', '/api/v2/plc/export/instances'):
            exported = client.post(path, headers=headers)
            assert exported.status_code == 200, exported.text
        from backend.v2_engine import export_workbook, import_workbook
        with client.app.state.session_factory() as session:
            exported_state = v2_store.load_project(session)
        excel = export_workbook(exported_state, 'EXCHANGES_EXC.xlsx')
        assert import_workbook(excel, 'EXCHANGES_EXC.xlsx', exported_state)['state'][KEY] == saved[KEY]
        current_revision = client.get('/api/v2/project').json()['revision']
        cleared = client.post('/api/v2/exchanges/preview', headers=headers, json={**body, 'revision': current_revision, 'assignments': []}).json()
        assert cleared.get('token'), cleared
        assert client.post('/api/v2/exchanges/apply', headers=headers, json={'token': cleared['token']}).status_code == 200
        assert client.get('/api/v2/project').json()[KEY] == []
        restore = client.post('/api/v2/workspace/history/restore/preview', headers=headers, json={
            'revision': client.get('/api/v2/project').json()['revision'], 'target_revision': saved['revision']}).json()
        assert restore.get('token'), restore
        restored = client.post('/api/v2/workspace/restore/apply', headers=headers, json={'token': restore['token']})
        assert restored.status_code == 200, restored.text
        assert client.get('/api/v2/project').json()[KEY] == saved[KEY]
        body['revision'] = saved['revision']
        body['assignments'] = []
        # A stale review cannot replace newer assignments.
        rejected = client.post('/api/v2/exchanges/preview', json={**body, 'revision': before['revision']}, headers=headers)
        assert rejected.status_code == 409
        assert client.post('/api/v2/workspace/projects', headers=headers, json={'name': 'Empty assignments project'}).status_code == 200
        assert client.get('/api/v2/project').json().get(KEY, []) == []
    finally:
        client.close()
