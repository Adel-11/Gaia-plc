"""HTTP acceptance: reviewed export bytes, ownership, CSRF and stale revisions."""
import io

import openpyxl
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from backend.main import create_app
from backend.db import User


def test_plc_export_requires_reviewable_valid_current_owned_preview(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'plc-reviewer')
    monkeypatch.setenv('ADMIN_PASSWORD', 'plc-review-password')
    app = create_app(data_dir=tmp_path, secret_key='plc-tests-' * 5, cookie_secure=False)
    with TestClient(app) as client:
        assert client.get('/api/v2/plc/options').status_code == 401
        auth = client.post('/api/login', json={'username': 'plc-reviewer', 'password': 'plc-review-password'})
        headers = {'X-CSRF-Token': auth.json()['csrf_token']}
        assert client.post('/api/v2/plc/preview', json={'mode': 'full'}).status_code == 403
        options = client.get('/api/v2/plc/options')
        assert options.status_code == 200, options.text
        assert any(x['value'] == 'instance:DIP1500_1' for x in options.json()['focus'])
        preview = client.post('/api/v2/plc/preview', headers=headers, json={'mode': 'full'})
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan['errors'] == [], plan['errors']
        assert 'DUT_Union_Real_Word' not in {r['name'] for r in plan['included']}
        token = plan['token']
        xml = client.get(f'/api/v2/plc/download/{token}')
        assert xml.status_code == 200, xml.text
        assert xml.content == client.get(f'/api/v2/plc/download/{token}').content
        assert b'<Application' not in xml.content
        report = client.get(f'/api/v2/plc/report/{token}')
        assert report.status_code == 200
        assert 'COPY' in report.text and 'GVL_PA' in report.text
        invalid = client.post('/api/v2/plc/preview', headers=headers,
                              json={'mode': 'partial', 'focus': 'object:GFC_EXC', 'selected': []}).json()
        assert invalid['errors']
        assert client.get(f"/api/v2/plc/download/{invalid['token']}").status_code == 409
        assert client.get(f"/api/v2/plc/report/{invalid['token']}").status_code == 200
        with app.state.session_factory() as session:
            session.add(User(username='another-reviewer', password_hash=PasswordHash.recommended().hash('another-review-password')))
            session.commit()
        client.post('/api/logout', headers=headers)
        other = client.post('/api/login', json={'username': 'another-reviewer', 'password': 'another-review-password'})
        assert other.status_code == 200
        assert client.get(f'/api/v2/plc/download/{token}').status_code == 404
        client.post('/api/logout', headers={'X-CSRF-Token': other.json()['csrf_token']})
        auth = client.post('/api/login', json={'username': 'plc-reviewer', 'password': 'plc-review-password'})
        headers = {'X-CSRF-Token': auth.json()['csrf_token']}
        # An unrelated engineering edit invalidates the reviewed XML as well.
        workbook = openpyxl.load_workbook(io.BytesIO(client.get('/api/v2/exports/TYPE_DIP1500.xlsx').content))
        workbook['TYPE']['B4'] = 'New revision after PLC preview'
        buffer = io.BytesIO()
        workbook.save(buffer)
        draft = client.post('/api/v2/imports/preview', headers=headers,
                            files={'file': ('TYPE_DIP1500.xlsx', buffer.getvalue())}, data={'mode': 'merge'}).json()
        assert client.post('/api/v2/imports/apply', headers=headers, json={'token': draft['token']}).status_code == 200
        assert client.get(f'/api/v2/plc/download/{token}').status_code == 409
        assert client.get(f'/api/v2/plc/report/{token}').status_code == 409
        assert client.get('/api/v2/plc/download/unknown').status_code == 404
        client.post('/api/logout', headers=headers)
        assert client.get(f'/api/v2/plc/download/{token}').status_code == 401
