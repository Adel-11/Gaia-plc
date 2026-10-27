"""Focused PLC packages and complete project folder navigation."""
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app


def test_folder_layout_counts_as_a_program_export_change():
    import copy
    from backend.v2_engine import load_pack
    from backend.v2_workspace import program_digest
    state = copy.deepcopy(load_pack())
    before = program_digest(state)
    state['object_folders'] = {'FB_INST_DIP1500': 'Pumps'}
    state['plc_folders'] = ['Pumps', 'Empty']
    assert program_digest(state) != before
    first = program_digest(state)
    state['plc_folders'].reverse()
    assert program_digest(state) == first


def _login(client):
    response = client.post('/api/login', json={'username': 'plc-tree', 'password': 'plc-tree-password'})
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json()['csrf_token']}


def test_focused_exports_tree_folder_move_and_guards(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'plc-tree')
    monkeypatch.setenv('ADMIN_PASSWORD', 'plc-tree-password')
    app = create_app(data_dir=tmp_path, secret_key='plc-tree-test-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        headers = _login(client)
        tree = client.get('/api/v2/plc/tree')
        assert tree.status_code == 200, tree.text
        revision = tree.json()['revision']
        fb_rows = [o for o in tree.json()['objects'] if o.get('type_id')]
        assert fb_rows and {'name', 'kind', 'folder', 'type_id', 'role'} <= set(fb_rows[0])
        assert client.post('/api/v2/plc/folders', json={'revision': revision, 'path': 'User/Empty'}).status_code == 403
        created = client.post('/api/v2/plc/folders', headers=headers,
                              json={'revision': revision, 'path': 'User/Empty'})
        assert created.status_code == 200, created.text
        revision = created.json()['revision']
        movable = tree.json()['objects'][0]['name']
        placed = client.post('/api/v2/plc/placement', headers=headers,
                             json={'revision': revision, 'name': movable, 'folder': 'User/Empty'})
        assert placed.status_code == 200, placed.text
        revision = placed.json()['revision']
        empty_xml = client.post('/api/v2/plc/export/type/not-a-type', headers=headers)
        assert empty_xml.status_code == 404

        # Folder paths persist as project state and moves rewrite all descendants.
        moved = client.post('/api/v2/plc/folders/move', headers=headers,
                            json={'revision': revision, 'path': 'User', 'destination': 'Function Blocs'})
        assert moved.status_code == 200, moved.text
        assert 'Function Blocs/User/Empty' in moved.json()['folders']
        from backend import v2_store
        with app.state.session_factory() as session:
            persisted = v2_store.load_project(session)
        assert persisted['object_folders'][movable] == 'Function Blocs/User/Empty'
        stale = client.post('/api/v2/plc/folders', headers=headers,
                            json={'revision': revision, 'path': 'Stale'})
        assert stale.status_code == 409
        assert client.get('/api/v2/plc/tree').json()['revision'] == moved.json()['revision']

        with app.state.session_factory() as session:
            state = v2_store.load_project(session)
            typ = state['types'][0]
            # An unrelated invalid exchange reference must not poison an isolated type package.
            state.setdefault('exchange_calls', []).append({'function_id': 'MISSING_FC', 'instance_a_id': 'ghost', 'instance_b_id': 'ghost'})
            state['exchange_functions'][0]['mappings'] = [{'source': 'missing.value', 'destination': 'missing.other'}]
            v2_store.save_project(session, state, username='test', expected_revision=state['revision'])
            session.commit()
            type_id = typ['id']
        assert client.get('/api/v2/plc/tree').status_code == 200
        assert client.get('/api/v2/plc/object/GVL_COM').status_code == 200
        exported = client.post(f'/api/v2/plc/export/type/{type_id}', headers=headers)
        assert exported.status_code == 200, exported.text
        root = etree.fromstring(exported.content)
        names = set(root.xpath('//*[local-name()="dataType" or local-name()="pou" or local-name()="globalVars"]/@name'))
        assert typ['fb']['name'] in names
        assert {d['name'] for d in typ['duts']} <= names
        assert not any(n.startswith(('GFC_', 'GFB_', 'PLC_PRG')) for n in names)
        version = client.get('/api/v2/project').json()['version']
        assert client.post(f'/api/v2/plc/export/type/{type_id}', headers=headers).content == exported.content
        assert client.get('/api/v2/project').json()['version'] == version

        with app.state.session_factory() as session:
            state = v2_store.load_project(session)
            pa = next(r for r in state['pa_values'] if (r.get('Member') or r.get('member_name')) == 'COMMUNICATION_ADDRESS')
            pa['value'] = pa['Instance value'] = 17
            pa_instance = pa.get('instance_id') or pa.get('instance') or pa.get('Instance')
            from backend.v2_program_membership import freeze_membership
            freeze_membership(state)['GFB_INST'] = []
            v2_store.save_project(session, state, username='test', expected_revision=state['revision'])
            session.commit()
        instances = client.post('/api/v2/plc/export/instances', headers=headers)
        assert instances.status_code == 200, instances.text
        inames = set(etree.fromstring(instances.content).xpath('//*[local-name()="dataType" or local-name()="pou" or local-name()="globalVars"]/@name'))
        assert 'GFB_INST' in inames or 'GFB_UPI' in inames
        assert not any(n.startswith('GFC_') or n == 'PLC_PRG' for n in inames)
        instance_root = etree.fromstring(instances.content)
        assert instance_root.xpath('//*[local-name()="globalVars" and @name="GVL_PA"]/*[local-name()="variable" and @name=$name]/*[local-name()="initialValue"]//*[local-name()="value" and @member="COMMUNICATION_ADDRESS"]/*[local-name()="simpleValue"]/@value', name=f'PA_{pa_instance}') == ['17']
        assert instance_root.xpath('//*[local-name()="pou" and @name="GFB_INST"]/*[local-name()="interface"]//*[local-name()="variable"]')

        # Folder organization is local to each project, including empty directories.
        assert client.post('/api/v2/workspace/projects', headers=headers, json={'name': 'Separate layout'}).status_code == 200
        second = client.get('/api/v2/project').json()
        assert second.get('object_folders', {}) == {}
        assert second.get('plc_folders', []) == []
        assert client.post('/api/v2/workspace/select', headers=headers, json={'id': persisted['project_id']}).status_code == 200

        assert client.get('/api/v2/plc/object/GVL_COM').status_code == 200
        assert client.post('/api/v2/plc/folders/move', headers=headers,
                           json={'revision': client.get('/api/v2/project').json()['revision'], 'path': 'Function Blocs/User', 'destination': 'Function Blocs/User/Empty'}).status_code == 422
