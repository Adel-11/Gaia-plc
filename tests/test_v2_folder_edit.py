from copy import deepcopy

import pytest
from lxml import etree

from backend import v2_plc, v2_store
from backend.v2_codesys import _ids, _objects, _st
from tests.test_v2_type_creation import _client


@pytest.fixture
def api(tmp_path, monkeypatch):
    client, headers = _client(tmp_path, monkeypatch)
    yield client, headers
    client.close()


def tree(client):
    return client.get('/api/v2/plc/tree').json()


def post(api, action, **values):
    client, headers = api
    return client.post('/api/v2/plc/' + action, headers=headers,
                       json={'revision': tree(client)['revision'], **values})


def persisted(client):
    with client.app.state.session_factory() as session:
        return deepcopy(v2_store.load_project(session))


def xml(state):
    state = deepcopy(state)
    objects = v2_plc.build_program(state)
    return etree.fromstring(v2_plc.serialize_program(objects, state, list(objects)))


def test_rename_and_delete_snapshot_folders_keep_every_object_and_export_the_new_paths(api):
    client, _ = api
    original = tree(client)
    src = 'Function Blocs'
    assert src in original['folders']
    assert post(api, 'folders', path=src + '/QA_EMPTY').status_code == 200
    before = persisted(client)
    original_xml = xml(before)
    renamed = post(api, 'folders/rename', path=src, name='QA_RENAMED')
    assert renamed.status_code == 200, renamed.text
    current = tree(client)
    assert 'QA_RENAMED/QA_EMPTY' in current['folders']
    assert not any(path == src or path.startswith(src + '/') for path in current['folders'])
    for row in original['objects']:
        expected = 'QA_RENAMED' + row['folder'][len(src):] if row['folder'] == src or row['folder'].startswith(src + '/') else row['folder']
        assert next(item for item in current['objects'] if item['name'] == row['name'])['folder'] == expected
    renamed_xml = xml(persisted(client))
    assert not any(path == src or path.startswith(src + '/') for path in v2_plc._folder_map(renamed_xml).values())
    deleted = post(api, 'folders/delete', path='QA_RENAMED')
    assert deleted.status_code == 200, deleted.text
    after = persisted(client)
    current = tree(client)
    assert 'QA_EMPTY' in current['folders']
    assert not any(path == 'QA_RENAMED' or path.startswith('QA_RENAMED/') for path in current['folders'])
    assert after['types'] == before['types'] and after['instances'] == before['instances']
    for key in ('tcp_points', 'rtu_channels', 'io_channels', 'exchange_calls', 'program_membership'):
        assert after.get(key) == before.get(key)
    final_xml = xml(after)
    assert _ids(final_xml, _objects(final_xml)) == _ids(original_xml, _objects(original_xml))
    assert {name: _st(element) for name, element in _objects(final_xml).items()} == {name: _st(element) for name, element in _objects(original_xml).items()}
    for row in original['objects']:
        expected = row['folder'][len(src):].lstrip('/') if row['folder'] == src or row['folder'].startswith(src + '/') else row['folder']
        assert next(item for item in current['objects'] if item['name'] == row['name'])['folder'] == expected
        assert v2_plc._folder_map(final_xml)[row['name']] == expected


def test_folder_collisions_invalid_paths_stale_revisions_and_csrf_are_rejected_without_changes(api):
    client, headers = api
    for path in ('QA_PARENT/Box/Leaf', 'QA_PARENT/Leaf'):
        assert post(api, 'folders', path=path).status_code == 200
    before = client.get('/api/v2/project').json()
    assert post(api, 'folders/delete', path='QA_PARENT/Box').status_code == 409
    assert post(api, 'folders/rename', path='QA_PARENT/Box', name='leaf').status_code == 409
    for name in ('', '..', '../Other', 'A/B', 'A\\B', '   '):
        assert post(api, 'folders/rename', path='QA_PARENT/Box', name=name).status_code == 422
    for action in ('rename', 'delete'):
        values = {'path': 'Missing', **({'name': 'Other'} if action == 'rename' else {})}
        assert post(api, 'folders/' + action, **values).status_code == 404
        values['path'] = 'QA_PARENT/Box'
        assert client.post('/api/v2/plc/folders/' + action, json={'revision': before['revision'], **values}).status_code == 403
        assert client.post('/api/v2/plc/folders/' + action, headers=headers,
                           json={'revision': before['revision'] - 1, **values}).status_code == 409
        assert post(api, 'folders/' + action, **{**values, 'path': '/'}).status_code == 422
    assert client.get('/api/v2/project').json() == before
    renamed = post(api, 'folders/rename', path='QA_PARENT/Box', name='box')
    assert renamed.status_code == 200
    assert 'QA_PARENT/box/Leaf' in tree(client)['folders']


def test_empty_folder_deletion_persists_and_project_layouts_remain_isolated(api):
    client, headers = api
    assert post(api, 'folders', path='QA_EMPTY').status_code == 200
    first = persisted(client)
    assert client.post('/api/v2/workspace/projects', headers=headers, json={'name': 'Independent folders'}).status_code == 200
    assert 'QA_EMPTY' not in tree(client)['folders']
    assert post(api, 'folders', path='QA_EMPTY').status_code == 200
    assert post(api, 'folders/rename', path='QA_EMPTY', name='QA_OTHER').status_code == 200
    assert post(api, 'folders/delete', path='QA_OTHER').status_code == 200
    assert 'QA_OTHER' not in tree(client)['folders']
    assert client.post('/api/v2/workspace/select', headers=headers, json={'id': first['project_id']}).status_code == 200
    assert 'QA_EMPTY' in tree(client)['folders']
    assert post(api, 'folders/delete', path='QA_EMPTY').status_code == 200
    assert 'QA_EMPTY' not in tree(client)['folders']
