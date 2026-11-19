from copy import deepcopy

import pytest

from backend.v2_hmi import load_hmi_pack
from backend.v2_hmi_domain import migrate_state, _rewrite, domain
from backend.v2_plc import build_program, NS, PLC
from backend.v2_workspace import _compatibility_errors


@pytest.fixture(scope='module')
def original():
    return deepcopy(load_hmi_pack())


def test_migration_preserves_addresses_and_panelview_tags_and_is_idempotent(original):
    from backend.v2_panelview_export import _tags
    state = deepcopy(original)
    before = set(_compatibility_errors(state))
    tags = list(_tags(state, 'PLC-1'))
    migrate_state(state)
    assert list(_tags(state, 'PLC-1')) == tags
    assert not set(_compatibility_errors(state)) - before
    assert state['instances'] == original['instances']
    for key in ('tcp_points', 'rtu_channels'):
        assert [(p['id'], p.get('address'), p.get('GVL_MODBUS variable')) for p in state[key]] == [
            (p['id'], p.get('address'), p.get('GVL_MODBUS variable')) for p in original[key]]
    objects = build_program(state)
    assert 'GVL_HMI' in objects
    for typ in state['types']:
        hmi = next(d for d in typ['duts'] if d['role'] == 'HMI')
        com = next(d for d in typ['duts'] if d['role'] == 'COM')
        assert not any(m['name'].startswith('HMI_') for m in com['members'])
        assert any(m['name'].startswith('HMI_') for m in hmi['members'])
        fb = objects[typ['fb']['name']]['element']
        assert fb.xpath('./p:interface/p:inOutVars/p:variable[@name="HMI"]/p:type/p:derived/@name', namespaces={'p': PLC}) == [hmi['name']]
        assert 'COM.HMI_' not in typ['fb']['body']
    once = deepcopy(state)
    migrate_state(state)
    assert state == once


def test_reference_rewrite_preserves_strings_comments_and_field_members():
    code = "COM.HMI_RUN := COM.FIELD_RUN; (* COM.HMI_RUN *)\n// COM.HMI_RUN\nlabel := 'COM.HMI_RUN';"
    result = _rewrite(code, ['HMI_RUN'])
    assert result == "HMI.HMI_RUN := COM.FIELD_RUN; (* COM.HMI_RUN *)\n// COM.HMI_RUN\nlabel := 'COM.HMI_RUN';"


def test_supervision_members_without_hmi_prefix_are_also_moved(original):
    state = deepcopy(original)
    typ = next(t for t in state['types'] if t['name'] == 'ADM100W')
    next(d for d in typ['duts'] if d['role'] == 'COM')['members'].append({'name': 'OUTPUT_Value', 'iec_type': 'BOOL'})
    row = deepcopy(next(p for p in state['tcp_points'] if p['Instance'] == 'ADM100W_1'))
    row['GVL_COM variable'] = 'GVL_COM.COM_ADM100W_1.OUTPUT_Value'
    row['id'] = 'qa-non-prefixed'
    state['tcp_points'].append(row)
    typ['fb']['body'] += '\nCOM.OUTPUT_Value := TRUE;\n'
    migrate_state(state)
    assert any(m['name'] == 'OUTPUT_Value' for d in typ['duts'] if d['role'] == 'HMI' for m in d['members'])
    assert 'HMI.OUTPUT_Value := TRUE;' in typ['fb']['body']
    assert next(p for p in state['tcp_points'] if p['id'] == row['id'])['GVL_COM variable'] == 'GVL_HMI.HMI_ADM100W_1.OUTPUT_Value'


def test_both_mapping_domains_share_free_addresses_and_templates_are_separate(original):
    from backend.v2_modbus_templates import apply_template
    state = deepcopy(original)
    migrate_state(state)
    typ = next(t for t in state['types'] if t['name'] == 'ADM100W')
    for role, name in [('COM', 'FIELD_TEST'), ('HMI', 'HMI_TEST')]:
        next(d for d in typ['duts'] if d['role'] == role)['members'].append({'name': name, 'iec_type': 'WORD'})
    typ['modbus_template'] = [
        {'role': role, 'member': name, 'protocol': 'tcp', 'table': 'holding_registers', 'inbound': True}
        for role, name in [('COM', 'FIELD_TEST'), ('HMI', 'HMI_TEST')]]
    field, _ = apply_template(state, 'ADM100W_1', 'tcp', 'HMI', role='COM')
    hmi, _ = apply_template(state, 'ADM100W_1', 'tcp', 'HMI', role='HMI')
    assert len(field) == len(hmi) == 1
    assert field[0]['address'] != hmi[0]['address']
    assert domain(field[0]) == 'COM' and domain(hmi[0]) == 'HMI'
    assert field[0]['GVL_COM variable'] == 'GVL_COM.COM_ADM100W_1.FIELD_TEST'
    assert hmi[0]['GVL_COM variable'] == 'GVL_HMI.HMI_ADM100W_1.HMI_TEST'
    from backend.v2_configuration import apply_changes
    with pytest.raises(ValueError):
        apply_changes(deepcopy(state), [{'op': 'create', 'collection': 'tcp_points', 'values': {
                     'Instance': 'ADM100W_1', 'Member': 'HMI.HMI_TEST', 'Table': 'holding_registers',
                     'Client': 'HMI', 'Modbus address': field[0]['address'], 'Direction': 'Client to PLC'}}])


def test_instance_deletion_removes_hmi_globals_and_copies(original):
    from backend.v2_instance_deletion import delete_instance
    state = deepcopy(original)
    migrate_state(state)
    delete_instance(state, next(i for i in state['instances'] if i['name'] == 'ADM100W_1'))
    objects = build_program(state)
    assert not objects['GVL_HMI']['element'].xpath('./p:variable[@name="HMI_ADM100W_1"]', namespaces={'p': PLC})
    assert not any('HMI_ADM100W_1.' in str(p.get('GVL_COM variable')) for p in state['tcp_points'])


def test_hmi_fb_links_and_member_renames_remain_editable(original):
    from backend.v2_fb_flow import update_flow, flow_view
    from backend.v2_member_sync import sync_consumers, sync_fb
    state = deepcopy(original)
    migrate_state(state)
    typ = next(t for t in state['types'] if t['name'] == 'ADM100W')
    row = {'section': 'inputs', 'source': 'HMI.HMI_INPUT_LOCAL_REQUEST', 'destination': 'INTERN.HMI_LOCAL_REQUEST'}
    assert update_flow(typ, state, mappings=[row]) == []
    assert any(link['source'] == row['source'] for link in flow_view(typ['fb'])['mappings'])
    member = next(m for d in typ['duts'] if d['role'] == 'HMI' for m in d['members'] if m['name'] == 'HMI_INPUT_LOCAL_REQUEST')
    member['name'] = 'HMI_INPUT_LOCAL_TEST'
    delta = {'HMI': {'hmi_input_local_request': member['name']}}
    mappings, errors, _ = sync_fb(typ, delta)
    assert not errors
    assert update_flow(typ, state, mappings=mappings) == []
    errors, _ = sync_consumers(state, typ, delta)
    assert not errors
    assert any(p['GVL_COM variable'].endswith('.HMI_INPUT_LOCAL_TEST') for p in state['tcp_points'])
    assert 'HMI.HMI_INPUT_LOCAL_TEST' in typ['fb']['body']


def test_database_migration_creates_an_integral_backup_and_keeps_review_choices(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path
    from tests.test_v2_type_creation import _client
    from backend import v2_store as store
    from backend.v2_hmi_domain import migrate_project
    from backend.v2_gfc_mappings import connection_key
    client, _ = _client(tmp_path, monkeypatch)
    try:
        app = client.app
        with app.state.session_factory() as session:
            state = store.load_project(session)
            row = next(p for p in state['tcp_points'] if '.HMI_' in p.get('GVL_COM variable', ''))
            key = connection_key('tcp_points', row)
            state.setdefault('gfc_copy_overrides', {}).setdefault('GFC_COM_IN', {})[key] = row['GVL_COM variable']
            saved = store.save_project(session, state, 'qa', state['revision'])
            session.commit()
            result = migrate_project(session, 1, saved['revision'], backup_dir=tmp_path / 'backups')
            session.commit()
            migrated = store.load_project(session)
            assert migrated['separate_hmi']
            assert migrated['gfc_copy_overrides']['GFC_COM_IN'][key].startswith('GVL_HMI.')
            with sqlite3.connect(result['backup']) as backup:
                assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                assert backup.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0
            assert Path(result['backup']).is_file()
            assert migrate_project(session, 1, migrated['revision'], backup_dir=tmp_path / 'backups')['already_migrated']
    finally:
        client.close()
