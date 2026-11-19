from copy import deepcopy

import pytest

from backend.v2_hmi import load_hmi_pack
from backend.v2_modbus_templates import apply_template


@pytest.fixture
def project():
    state = deepcopy(load_hmi_pack())
    state['types'].append({'id': 'QA_ALLOC', 'name': 'QA_ALLOC', 'duts': [
        {'name': 'DUT_COM_QA_ALLOC', 'role': 'COM', 'members': [
            {'name': name, 'iec_type': kind} for name, kind in [('RUN', 'BOOL'), ('READY', 'BOOL'),
                ('MEASURE', 'REAL'), ('COUNT', 'WORD'), ('SETPOINT', 'REAL')]]}],
        'modbus_template': [
            {'protocol': 'tcp', 'table': table, 'member': member, 'inbound': inbound, 'order': 'MSW_FIRST'}
            for table, member, inbound in [('coils', 'RUN', True), ('discrete_inputs', 'READY', False),
                ('input_registers', 'MEASURE', False), ('input_registers', 'COUNT', False),
                ('holding_registers', 'SETPOINT', True)]]})
    state['instances'].append({'id': 'QA_1', 'name': 'QA_1', 'type_id': 'QA_ALLOC'})
    return state


STARTS = {'coils': 500, 'discrete_inputs': 10500, 'input_registers': 500, 'holding_registers': 700}


def test_custom_table_starts_skip_all_domains_and_keep_wide_values_contiguous(project):
    # All clients/domains occupy the same PLC table. Two words of a REAL stay together.
    project['tcp_points'].extend([
        {'table': 'input_registers', 'Modbus address': address, 'Client': 'Other', 'role': 'HMI'}
        for address in (500, 502)])
    added, _ = apply_template(project, 'QA_1', 'tcp', 'HMI', allocation='from_address', start_addresses=STARTS)
    by_table = {table: [row['address'] for row in added if row['table'] == table] for table in STARTS}
    assert by_table == {'coils': [500], 'discrete_inputs': [10500], 'input_registers': [503, 504, 505], 'holding_registers': [700, 701]}
    pair = [row for row in added if row['member_name'] == 'MEASURE']
    assert [row['Word'] for row in pair] == ['MSW', 'LSW']
    assert pair[0]['GVL_COM variable'] == pair[1]['GVL_COM variable'] == 'GVL_COM.COM_QA_1.MEASURE'
    before = deepcopy(project['tcp_points'])
    added, summary = apply_template(project, 'QA_1', 'tcp', 'HMI', allocation='from_address',
                                     start_addresses={**STARTS, 'input_registers': 800})
    assert not added and project['tcp_points'] == before
    assert any('(existant)' in line for line in summary)


def test_default_still_uses_first_free_and_never_reallocates_existing_points(project):
    original = deepcopy(project['tcp_points'])
    first_free = next(number for number in range(1, 1502) if all(
        row.get('Modbus address', row.get('address')) != number or row.get('table') != 'coils'
        for row in original))
    added, _ = apply_template(project, 'QA_1', 'tcp', 'HMI')
    assert next(row for row in added if row['table'] == 'coils')['address'] == first_free
    assert project['tcp_points'][:len(original)] == original


@pytest.mark.parametrize('start', [None, True, '500', 500.5, -1, 1502])
def test_custom_start_requires_integer_inside_declared_capacity(project, start):
    with pytest.raises(ValueError, match='Adresse de départ input_registers'):
        apply_template(project, 'QA_1', 'tcp', 'HMI', allocation='from_address',
                       start_addresses={**STARTS, 'input_registers': start})


def test_custom_start_respects_client_range_and_never_wraps_to_lower_addresses(project):
    project['types'][-1]['modbus_template'] = [project['types'][-1]['modbus_template'][2]]
    client = next(row for row in project['tcp_clients'] if row['Client'] == 'HMI')
    client['IR'] = [400, 600]
    with pytest.raises(ValueError, match='entre 400 et 600'):
        apply_template(project, 'QA_1', 'tcp', 'HMI', allocation='from_address', start_addresses={'input_registers': 300})
    with pytest.raises(ValueError, match='Aucune adresse libre'):
        apply_template(project, 'QA_1', 'tcp', 'HMI', allocation='from_address', start_addresses={'input_registers': 600})


def test_default_rtu_keeps_manufacturer_registers_and_rejects_custom_relocation(project):
    project['types'][-1]['modbus_template'] = [{'protocol': 'rtu', 'table': 'input_registers',
        'member': 'MEASURE', 'inbound': True, 'register': 42, 'order': 'MSW_FIRST'}]
    line = project['rtu_lines'][0]['Line']
    with pytest.raises(ValueError, match='réservé au modèle TCP'):
        apply_template(project, 'QA_1', 'rtu', line=line, address=30,
                       allocation='from_address', start_addresses={'input_registers': 100})
    added, _ = apply_template(project, 'QA_1', 'rtu', line=line, address=30)
    assert [row['Equipment address'] for row in added] == [42, 43]


def test_reviewed_api_uses_chosen_start_and_rejects_invalid_start_without_saving(tmp_path, monkeypatch):
    from tests.test_v2_type_creation import _client
    from backend import v2_store as store
    from backend.v2_fb_flow import variable_options
    client, headers = _client(tmp_path, monkeypatch)
    try:
        with client.app.state.session_factory() as session:
            state = store.load_project(session)
            typ = next(row for row in state['types'] if row['name'] == 'DIP1500')
            member = next(row['path'][4:] for row in variable_options(typ, state) if row['path'].startswith('COM.') and row['iec_type'] == 'BOOL')
            typ['modbus_template'] = [{'protocol': 'tcp', 'table': 'coils', 'member': member, 'inbound': True}]
            state['instances'].append({'id': 'QA_DIP', 'name': 'QA_DIP', 'type_id': typ['id']})
            store.save_project(session, state, username='test', expected_revision=state['revision'])
            session.commit()
        before = client.get('/api/v2/project').json()
        body = {'revision': before['revision'], 'changes': [], 'modbus_template': {
            'instance_id': 'QA_DIP', 'protocol': 'tcp', 'client': 'HMI',
            'allocation': 'from_address', 'start_addresses': {'coils': 1200}}}
        preview = client.post('/api/v2/configuration/preview', json=body, headers=headers).json()
        assert not preview['errors'] and preview['token'], preview
        assert client.get('/api/v2/project').json() == before
        proposed = [row for row in preview['configuration']['tcp_points'] if row.get('Instance') == 'QA_DIP']
        assert len(proposed) == 1 and proposed[0]['Modbus address'] == 1200
        body['modbus_template']['start_addresses']['coils'] = 0
        rejected = client.post('/api/v2/configuration/preview', json=body, headers=headers).json()
        assert rejected['errors'] and not rejected['token']
        assert client.get('/api/v2/project').json() == before
        applied = client.post('/api/v2/configuration/apply', json={'token': preview['token']}, headers=headers)
        assert applied.status_code == 200, applied.text
        saved = client.get('/api/v2/project').json()
        assert next(row for row in saved['tcp_points'] if row.get('Instance') == 'QA_DIP')['Modbus address'] == 1200
    finally:
        client.close()
