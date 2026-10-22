"""Stage-four HTTP edits must flow through persistence and real PLCopen exports."""
from fastapi.testclient import TestClient
from lxml import etree
from backend.main import create_app


def test_object_edit_reaches_xml_and_invalidates_old_exports(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'object-acceptance')
    monkeypatch.setenv('ADMIN_PASSWORD', 'object-acceptance-password')
    app = create_app(data_dir=tmp_path, secret_key='object-acceptance-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post('/api/login', json={'username': 'object-acceptance', 'password': 'object-acceptance-password'})
        headers = {'X-CSRF-Token': auth.json()['csrf_token']}
        old_export = client.post('/api/v2/plc/preview', headers=headers, json={'mode': 'full'}).json()
        view = client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()
        duts = [{'name': d['name'], 'members': [dict(m) for m in d['members']]} for d in view['duts']]
        internal = next(d for d in duts if d['name'] == 'DUT_INT_UPI_LOCAL_FUXA')
        internal['members'].append({'name': 'EDITOR_FLAG', 'iec_type': 'BOOL', 'default_value': False, 'comment': 'Editor acceptance'})
        custom = 'INTERN.EDITOR_FLAG := TRUE;\r\n// preserved custom section\r\n'
        response = client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview', headers=headers,
            json={'revision': view['revision'], 'duts': duts, 'body': view['fb']['body'], 'custom_code': custom})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['errors'] == [], preview
        assert client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()['revision'] == view['revision']
        applied = client.post('/api/v2/objects/apply', headers=headers, json={'token': preview['token']})
        assert applied.status_code == 200, applied.text
        assert client.get('/api/v2/plc/download/' + old_export['token']).status_code == 409
        saved = client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()
        assert saved['fb']['custom_code'] == custom
        assert 'EDITOR_FLAG' in next(d['st'] for d in saved['duts'] if d['role'] == 'INT')
        generated = client.post('/api/v2/plc/preview', headers=headers, json={'mode': 'full'}).json()
        assert generated['errors'] == [], generated['errors']
        xml = client.get('/api/v2/plc/download/' + generated['token'])
        assert xml.status_code == 200
        root = etree.fromstring(xml.content)
        fb = root.xpath("//*[local-name()='pou'][@name='FB_UPI_LOCAL_FUXA']//*[local-name()='ST']/*[local-name()='xhtml']")[0]
        assert custom in fb.text
        assert not root.xpath("//*[local-name()='Object'][@Name='Application']")
        assert root.xpath("//*[local-name()='dataType'][@name='DUT_INT_UPI_LOCAL_FUXA']//*[local-name()='variable'][@name='EDITOR_FLAG']")

def test_invalid_object_drafts_and_stale_apply_never_change_project(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'object-guard')
    monkeypatch.setenv('ADMIN_PASSWORD', 'object-guard-password')
    app = create_app(data_dir=tmp_path, secret_key='object-guards-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post('/api/login', json={'username': 'object-guard', 'password': 'object-guard-password'})
        headers = {'X-CSRF-Token': auth.json()['csrf_token']}
        view = client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()
        for member in [
            {'name': 'INT', 'iec_type': 'BOOL'},
            {'name': 'BAD_DEFAULT', 'iec_type': 'WORD', 'default_value': '70000'},
            {'name': 'BAD_ARRAY', 'iec_type': 'ARRAY[10..1] OF WORD'},
            {'name': 'BAD_TYPE', 'iec_type': 'DUT_DOES_NOT_EXIST'},
        ]:
            duts = [{'name': d['name'], 'members': [dict(m) for m in d['members']]} for d in view['duts']]
            next(d for d in duts if d['name'] == 'DUT_INT_UPI_LOCAL_FUXA')['members'].append(member)
            rejected = client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview', headers=headers, json={'revision': view['revision'], 'duts': duts})
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()['errors'] and not rejected.json()['token']
        assert client.get('/api/v2/project').json()['revision'] == view['revision']
        first = client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview', headers=headers,
            json={'revision': view['revision'], 'custom_code': '// first edit'}).json()
        second = client.post('/api/v2/objects/type/UPI_LOCAL_FUXA/preview', headers=headers,
            json={'revision': view['revision'], 'custom_code': '// second edit'}).json()
        assert first['errors'] == second['errors'] == []
        assert client.post('/api/v2/objects/apply', headers=headers, json={'token': first['token']}).status_code == 200
        assert client.post('/api/v2/objects/apply', headers=headers, json={'token': second['token']}).status_code == 409
        assert client.get('/api/v2/objects/type/UPI_LOCAL_FUXA').json()['fb']['custom_code'] == '// first edit'
