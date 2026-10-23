"""Exchange graph → workbook → complete/partial PLCopen acceptance."""
import io

import openpyxl
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app


def test_condition_else_custom_layout_and_new_call_survive_excel_and_xml(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ADMIN_USERNAME', 'stage6-admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'stage6-local-tests')
    app = create_app(data_dir=tmp_path, secret_key='stage6-tests-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post('/api/login', json={'username': 'stage6-admin', 'password': 'stage6-local-tests'}).json()
        headers = {'X-CSRF-Token': login['csrf_token']}

        def graph():
            response = client.get('/api/v2/exchanges')
            assert response.status_code == 200, response.text
            return response.json()

        def save(action, **fields):
            response = client.post('/api/v2/exchanges/preview', headers=headers,
                                   json={'revision': graph()['revision'], 'action': action, **fields})
            assert response.status_code == 200, response.text
            preview = response.json()
            assert not preview['errors'], preview
            applied = client.post('/api/v2/exchanges/apply', headers=headers, json={'token': preview['token']})
            assert applied.status_code == 200, applied.text
            return preview

        before = graph()
        save('connect', instance_a_id='DOSENSOR_1', instance_b_id='DIP1500_1')
        fn = next(f for f in graph()['functions'] if f['type_a_id'] == 'DOSENSOR' and f['type_b_id'] == 'DIP1500')
        assert len(graph()['functions']) == len(before['functions']) + 1
        mapping = {'id': 'signal-to-speed', 'source': fn['parameter_a'] + '.EXC_OUTPUT_SIGNAL',
                   'destination': fn['parameter_b'] + '.UPI_INPUT_SPEED',
                   'condition': {'mode': 'all', 'terms': [{'left': fn['parameter_a'] + '.EXC_OUTPUT_SIGNAL',
                                 'operator': '>', 'right': {'kind': 'literal', 'value': '2.0'}}]},
                   'else_value': {'kind': 'literal', 'value': '0.0'}}
        custom = '// Preserve this custom section\r\n' + fn['parameter_b'] + '.UPI_INPUT_RUN := TRUE;\r\n'
        save('save_function', function_id=fn['id'], mappings=[mapping], custom_code=custom)
        layout = {'nodes': {'DOSENSOR_1': {'x': 125, 'y': 80}, 'DIP1500_1': {'x': 740, 'y': 320}},
                  'viewport': {'x': -15, 'y': 22, 'zoom': 1.2}}
        save('layout', layout=layout)

        workbook = client.get('/api/v2/exports/EXCHANGES_EXC.xlsx')
        assert workbook.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(workbook.content))
        assert 'Condition' in [c.value for c in wb[fn['sheet']][1]]
        imported = client.post('/api/v2/imports/preview', headers=headers,
                               files={'file': ('EXCHANGES_EXC.xlsx', workbook.content)}, data={'mode': 'replace'})
        assert imported.status_code == 200, imported.text
        preview = imported.json()
        assert not preview['validation']['errors'], preview
        assert client.post('/api/v2/imports/apply', headers=headers, json={'token': preview['token']}).status_code == 200
        after = graph()
        restored = next(f for f in after['functions'] if f['id'] == fn['id'])
        assert restored['mappings'][0]['condition'] == mapping['condition']
        assert restored['mappings'][0]['else_value'] == mapping['else_value']
        assert restored['custom_code'] == custom
        assert after['layout'] == layout
        assert any(c['function_id'] == fn['id'] for c in after['calls'])

        for mode in ('full', 'partial'):
            payload = {'mode': mode}
            if mode == 'partial': payload['focus'] = 'object:' + fn['name']
            planned = client.post('/api/v2/plc/preview', headers=headers, json=payload).json()
            assert not planned['errors'], planned
            included = {o['name'] for o in planned['included']}
            assert fn['name'] in included and 'GFC_EXC' in included
            xml = client.get('/api/v2/plc/download/' + planned['token'])
            assert xml.status_code == 200
            root = etree.fromstring(xml.content)
            body = root.xpath("//*[local-name()='pou' and @name=$name]/*[local-name()='body']/*[local-name()='ST']/*[local-name()='xhtml']/text()", name=fn['name'])[0]
            assert 'IF ' in body and 'ELSE' in body and 'END_IF;' in body
            assert custom in body and body.count('Preserve this custom section') == 1

        # Malformed graph metadata is an import error, never silently discarded.
        column = next(c.column for c in wb[fn['sheet']][1] if c.value == 'Condition')
        wb[fn['sheet']].cell(2, column).value = '{broken json'
        invalid = io.BytesIO(); wb.save(invalid)
        result = client.post('/api/v2/imports/preview', headers=headers,
                             files={'file': ('EXCHANGES_EXC.xlsx', invalid.getvalue())}, data={'mode': 'replace'})
        assert result.status_code == 200, result.text
        assert result.json()['validation']['errors']
        assert graph()['revision'] == after['revision']
