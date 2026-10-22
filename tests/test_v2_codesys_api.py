"""Authenticated review and apply behavior for CODESYS snapshots."""
from __future__ import annotations

import copy

from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from backend.db import User
from backend.main import create_app


def _login(client, username='codesys-reviewer', password='codesys-review-password'):
    response = client.post('/api/login', json={'username': username, 'password': password})
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json()['csrf_token']}


def _install_conflicting_model(app, monkeypatch):
    from backend import v2_codesys, v2_store

    with app.state.session_factory() as session:
        state = v2_store.load_project(session)
        state['types'].append({'id': 'test-type', 'name': 'TEST', 'duts': [],
                               'fb': {'name': 'FB_TEST', 'declaration': 'VAR_INPUT x : BOOL; END_VAR',
                                      'body': 'generated();', 'custom_code': 'custom();'}})
        v2_store.save_project(session, state, username='test', expected_revision=state['revision'])
        session.commit()

    def preview(data, filename, current, scope='partial'):
        proposed = copy.deepcopy(current)
        proposed['snapshot'] = {'filename': filename, 'xml': '<incoming/>', 'objects': []}
        proposed['codesys_conflicts'] = {'FB_TEST': {'snapshot_fingerprint': 'imported',
                                                     'model_fingerprint': 'generated'}}
        return {'state': proposed, 'errors': [], 'warnings': [], 'changes': [{
            'name': 'FB_TEST', 'kind': 'POU', 'status': 'conflict', 'identity_changed': False,
            'folder_changed': False, 'codesys_changed': True, 'generator_changed': True,
            'conflict': True, 'can_adopt': True, 'before_st': '',
            'imported_st': 'codesys_body();', 'generated_st': 'generated();'}]}

    monkeypatch.setattr(v2_codesys, 'preview_codesys', preview)


def test_codesys_review_requires_auth_csrf_resolution_and_is_one_time(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'codesys-reviewer')
    monkeypatch.setenv('ADMIN_PASSWORD', 'codesys-review-password')
    app = create_app(data_dir=tmp_path, secret_key='codesys-tests-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        _install_conflicting_model(app, monkeypatch)
        with app.state.session_factory() as session:
            session.add(User(username='other-reviewer', password_hash=PasswordHash.recommended().hash('other-password')))
            session.commit()
        assert client.get('/api/v2/project').status_code == 401
        assert client.post('/api/v2/codesys/preview', files={'file': ('snapshot.xml', b'<xml/>')}).status_code == 401
        headers = _login(client)
        assert client.post('/api/v2/codesys/preview', files={'file': ('snapshot.xml', b'<xml/>')}).status_code == 403
        before = client.get('/api/v2/project').json()
        assert before['phase'] >= 3
        preview = client.post('/api/v2/codesys/preview', headers=headers,
                              files={'file': ('snapshot.xml', b'<xml/>')}).json()
        assert preview['revision'] == before['revision']
        assert preview['changes'][0]['conflict']
        assert client.get('/api/v2/project').json()['revision'] == before['revision']
        assert client.post('/api/v2/codesys/apply', headers=headers,
                           json={'token': preview['token']}).status_code == 422

        other = TestClient(app)
        other_headers = _login(other, 'other-reviewer', 'other-password')
        assert other.post('/api/v2/codesys/apply', headers=other_headers,
                          json={'token': preview['token'], 'resolutions': {'FB_TEST': 'keep_generator'}}).status_code == 404
        applied = client.post('/api/v2/codesys/apply', headers=headers,
                              json={'token': preview['token'], 'resolutions': {'FB_TEST': 'keep_generator'}})
        assert applied.status_code == 200, applied.text
        project = client.get('/api/v2/project').json()
        assert project['revision'] == before['revision'] + 1
        assert client.post('/api/v2/codesys/apply', headers=headers,
                           json={'token': preview['token'], 'resolutions': {'FB_TEST': 'keep_generator'}}).status_code == 404


def test_codesys_adoption_changes_only_existing_fb_body_and_rejects_unsafe_adoption(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'codesys-reviewer')
    monkeypatch.setenv('ADMIN_PASSWORD', 'codesys-review-password')
    app = create_app(data_dir=tmp_path, secret_key='codesys-tests-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        _install_conflicting_model(app, monkeypatch)
        headers = _login(client)
        preview = client.post('/api/v2/codesys/preview', headers=headers,
                              files={'file': ('snapshot.xml', b'<xml/>')}).json()
        adopted = client.post('/api/v2/codesys/apply', headers=headers,
                              json={'token': preview['token'], 'resolutions': {'FB_TEST': 'adopt_codesys'}})
        assert adopted.status_code == 200, adopted.text
        from backend import v2_store
        with app.state.session_factory() as session:
            state = v2_store.load_project(session)
        fb = next(t['fb'] for t in state['types'] if t['id'] == 'test-type')
        assert fb == {'name': 'FB_TEST', 'declaration': 'VAR_INPUT x : BOOL; END_VAR',
                      'body': 'codesys_body();', 'custom_code': 'custom();'}

        preview = client.post('/api/v2/codesys/preview', headers=headers,
                              files={'file': ('snapshot.xml', b'<xml/>')}).json()
        # A conflict that changes the interface cannot be accepted into the model.
        from backend.db import ImportPreview
        import json
        with app.state.session_factory() as session:
            row = session.get(ImportPreview, preview['token'])
            metadata = json.loads(row.diff_payload)
            metadata['changes'][0]['can_adopt'] = False
            row.diff_payload = json.dumps(metadata)
            session.commit()
        response = client.post('/api/v2/codesys/apply', headers=headers,
                               json={'token': preview['token'], 'resolutions': {'FB_TEST': 'adopt_codesys'}})
        assert response.status_code == 422


def test_codesys_preview_token_rejects_stale_revision(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'codesys-reviewer')
    monkeypatch.setenv('ADMIN_PASSWORD', 'codesys-review-password')
    app = create_app(data_dir=tmp_path, secret_key='codesys-tests-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        _install_conflicting_model(app, monkeypatch)
        headers = _login(client)
        preview = client.post('/api/v2/codesys/preview', headers=headers,
                              files={'file': ('snapshot.xml', b'<xml/>')}).json()
        from backend import v2_store
        with app.state.session_factory() as session:
            state = v2_store.load_project(session)
            v2_store.save_project(session, state, username='concurrent-edit', expected_revision=state['revision'])
            session.commit()
        response = client.post('/api/v2/codesys/apply', headers=headers,
                               json={'token': preview['token'], 'resolutions': {'FB_TEST': 'keep_generator'}})
        assert response.status_code == 409
