from copy import deepcopy

from lxml import etree

from backend import v2_plc, v2_store
from backend.v2_hmi_domain import migrate_state
from tests.test_v2_type_creation import _client


def test_generated_hmi_global_is_visible_without_compiling_and_folder_edits_reach_xml(tmp_path, monkeypatch):
    client, headers = _client(tmp_path, monkeypatch)
    try:
        with client.app.state.session_factory() as session:
            state = v2_store.load_project(session)
            assert migrate_state(state)
            v2_store.save_project(session, state, username='test', expected_revision=state['revision'])
            session.commit()
        before = client.get('/api/v2/project').json()
        with monkeypatch.context() as scope:
            def compilation_unavailable(*args, **kwargs):
                raise ValueError('Unrelated communication mapping is incomplete')
            scope.setattr(v2_plc, 'build_program', compilation_unavailable)
            response = client.get('/api/v2/plc/tree')
            assert response.status_code == 200, response.text
            tree = response.json()
        rows = {row['name']: row for row in tree['objects']}
        assert [row['name'] for row in tree['objects']].count('GVL_HMI') == 1
        assert rows['GVL_HMI']['kind'] == 'GVL'
        assert rows['GVL_HMI']['folder'] == rows['GVL_COM']['folder']
        assert client.get('/api/v2/project').json() == before
        inspected = client.get('/api/v2/plc/object/GVL_HMI')
        assert inspected.status_code == 200, inspected.text
        assert 'HMI_' in inspected.json()['declaration']

        def post(action, **body):
            revision = client.get('/api/v2/plc/tree').json()['revision']
            result = client.post('/api/v2/plc/' + action, headers=headers, json={'revision': revision, **body})
            assert result.status_code == 200, result.text
            return result.json()

        post('folders', path='QA_HMI/Supervision')
        post('placement', name='GVL_HMI', folder='QA_HMI/Supervision')
        post('folders/rename', path='QA_HMI/Supervision', name='PanelView')
        post('folders/delete', path='QA_HMI/PanelView')
        current = client.get('/api/v2/plc/tree').json()
        hmi = next(row for row in current['objects'] if row['name'] == 'GVL_HMI')
        assert hmi['folder'] == 'QA_HMI'
        assert client.get('/api/v2/plc/object/GVL_HMI').json()['folder'] == 'QA_HMI'
        with client.app.state.session_factory() as session:
            saved = deepcopy(v2_store.load_project(session))
        objects = v2_plc.build_program(saved)
        assert objects['GVL_HMI']['folder'] == 'QA_HMI'
        root = etree.fromstring(v2_plc.serialize_program(objects, saved, ['GVL_HMI']))
        assert v2_plc._folder_map(root)['GVL_HMI'] == 'QA_HMI'
        assert root.xpath('//*[local-name()="globalVars"]/@name') == ['GVL_HMI']
        assert saved['tcp_points'] == before['tcp_points']
        assert saved['types'] == before['types']
    finally:
        client.close()


def test_tree_covers_all_generated_objects_and_matches_exported_folders_for_every_profile(tmp_path, monkeypatch):
    from backend.v2_engine import load_pack
    from backend.v2_hmi import load_hmi_pack
    client, _ = _client(tmp_path, monkeypatch)
    try:
        legacy = deepcopy(load_pack())
        panelview = deepcopy(load_hmi_pack())
        separated = deepcopy(panelview)
        migrate_state(separated)
        extended = deepcopy(separated)
        typ = deepcopy(next(item for item in extended['types'] if item['name'] == 'DOSENSOR'))
        typ.update(id='QA_TREE_SENSOR', name='QA_TREE_SENSOR', folder='New equipment/Sensors')
        for dut in typ['duts']:
            dut.update(id='QA_TREE_SENSOR:' + dut['role'], name='DUT_' + dut['role'] + '_QA_TREE_SENSOR')
        typ['fb'] = {'name': 'FB_INST_QA_TREE_SENSOR', 'body': '// New model'}
        extended['types'].append(typ)
        extended['exchange_functions'].append({'name': 'FC_EXC_QA_TREE', 'type_a_id': 'DOSENSOR', 'type_b_id': 'QA_TREE_SENSOR'})
        for profile, state in [('legacy', legacy), ('panelview', panelview), ('separated', separated), ('new_objects', extended)]:
            # Imported locations and explicit root placements must agree as well.
            dut_name = state['types'][0]['duts'][0]['name']
            state.setdefault('object_folders', {}).update({'GVL_HMI': 'Custom/Supervision', dut_name: ''})
            snapshot = next((row for row in state.get('snapshot', {}).get('objects', []) if row.get('name') == 'GVL_IO'), None)
            if snapshot is not None:
                snapshot['folder'] = 'Imported/IO'
            original = deepcopy(state)
            with monkeypatch.context() as scope:
                scope.setattr(v2_store, 'load_project', lambda session: deepcopy(state))
                response = client.get('/api/v2/plc/tree')
                assert response.status_code == 200, (profile, response.text)
                rows = response.json()['objects']
            program = v2_plc.build_program(deepcopy(state), include_union=True)
            assert len(rows) == len({row['name'] for row in rows}), profile
            assert {row['name'] for row in rows} == set(program), profile
            output = etree.fromstring(v2_plc.serialize_program(program, state, list(program)))
            paths = v2_plc._folder_map(output)
            for row in rows:
                assert row['folder'] == program[row['name']]['folder'] == paths[row['name']], (profile, row['name'])
                assert (row['kind'] in {'GVL', 'DUT'}) == (program[row['name']]['kind'] in {'GVL', 'DUT'})
            assert state == original
    finally:
        client.close()
