"""Real snapshot import through HTTP, persistence and regenerated PLCopen."""
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app
from backend.v2_plc import build_program, serialize_program
from backend import v2_store


def test_snapshot_header_elements_and_explicit_root_folder_survive_export():
    from backend.v2_engine import load_pack
    from backend.v2_codesys import preview_codesys
    state = load_pack()
    objects = build_program(state)
    root = etree.fromstring(serialize_program(objects, state, list(objects)))
    scaling = root.xpath("./*[local-name()='contentHeader']/*[local-name()='coordinateInfo']//*[local-name()='scaling']")[0]
    scaling.set('x', '2')
    structure = root.xpath("//*[local-name()='ProjectStructure']")[0]
    obj = structure.xpath(".//*[local-name()='Object'][@Name='GVL_COM']")[0]
    obj.getparent().remove(obj)
    structure.append(obj)
    result = preview_codesys(etree.tostring(root), 'header-folder.xml', state)
    assert result['errors'] == []
    proposed = result['state']
    assert next(o['folder'] for o in proposed['snapshot']['objects'] if o['name'] == 'GVL_COM') == ''
    exported = etree.fromstring(serialize_program(build_program(proposed), proposed, ['GVL_COM']))
    assert exported.xpath("./*[local-name()='contentHeader']/*[local-name()='coordinateInfo']//*[local-name()='scaling']/@x")[0] == '2'
    assert exported.xpath("//*[local-name()='ProjectStructure']/*[local-name()='Object'][@Name='GVL_COM']")


def test_real_codesys_body_review_adoption_and_export_survive_restart(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'snapshot-admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'snapshot-password')
    app = create_app(data_dir=tmp_path, secret_key='snapshot-test-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post('/api/login', json={'username': 'snapshot-admin', 'password': 'snapshot-password'})
        headers = {'X-CSRF-Token': auth.json()['csrf_token']}
        with app.state.session_factory() as session:
            original = v2_store.load_project(session)
        objects = build_program(original)
        root = etree.fromstring(serialize_program(objects, original, list(objects)))
        body = root.xpath("//*[local-name()='pou'][@name='FB_UPI_LOCAL_FUXA']//*[local-name()='ST']/*[local-name()='xhtml']")[0]
        body.text += '\n// Edited in CODESYS\nCOM.SCADA_OUTPUT_LOCAL_ACTIVE := FALSE;\n'
        expected_body = body.text
        raw = etree.tostring(root, xml_declaration=True, encoding='utf-8')
        before = client.get('/api/v2/project').json()['revision']
        response = client.post('/api/v2/codesys/preview', headers=headers, files={'file': ('actual.xml', raw)})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['errors'] == []
        change = next(row for row in preview['changes'] if row['name'] == 'FB_UPI_LOCAL_FUXA')
        assert change['conflict'] and change['can_adopt']
        assert client.get('/api/v2/project').json()['revision'] == before
        assert client.post('/api/v2/codesys/apply', headers=headers, json={'token': preview['token']}).status_code == 422
        result = client.post('/api/v2/codesys/apply', headers=headers,
                             json={'token': preview['token'], 'resolutions': {'FB_UPI_LOCAL_FUXA': 'adopt_codesys'}})
        assert result.status_code == 200, result.text
        with app.state.session_factory() as session:
            saved = v2_store.load_project(session)
        assert saved['workbooks'] == original['workbooks']
        assert saved['codesys_conflicts'] == {}
        assert next(t['fb']['body'] for t in saved['types'] if t['fb']['name'] == 'FB_UPI_LOCAL_FUXA') == expected_body
        plan = client.post('/api/v2/plc/preview', headers=headers, json={'mode': 'full'}).json()
        assert plan['errors'] == [], plan['errors']
        exported = client.get('/api/v2/plc/download/' + plan['token'])
        assert exported.status_code == 200
        assert b'COM.SCADA_OUTPUT_LOCAL_ACTIVE := FALSE;' in exported.content
    restarted = create_app(data_dir=tmp_path, secret_key='snapshot-test-' * 4, cookie_secure=False)
    with TestClient(restarted) as client:
        assert client.post('/api/login', json={'username': 'snapshot-admin', 'password': 'snapshot-password'}).status_code == 200
        project = client.get('/api/v2/project').json()
        assert project['snapshot']['filename'] == 'actual.xml'
        assert project['snapshot']['object_count'] == 55
