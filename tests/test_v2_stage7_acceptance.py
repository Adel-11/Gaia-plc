"""Configuration preview lifecycle and export regression coverage."""
import copy
from fastapi.testclient import TestClient


def test_configuration_review_revision_and_workbook_roundtrip(tmp_path, monkeypatch):
    from backend.main import create_app
    from backend import v2_engine as engine
    from backend import v2_store as store
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','config-admin')
    monkeypatch.setenv('ADMIN_PASSWORD','config-local-tests')
    app=create_app(data_dir=tmp_path,secret_key='configuration-test-'*4,cookie_secure=False)
    with TestClient(app) as client:
        assert client.get('/api/v2/configuration').status_code==401
        login=client.post('/api/login',json={'username':'config-admin','password':'config-local-tests'})
        headers={'X-CSRF-Token':login.json()['csrf_token']}
        data=client.get('/api/v2/configuration').json()
        assert all(len(c['ranges'])==4 for c in data['tcp_clients'])
        pa=data['pa_values'][0]
        line=data['rtu_lines'][0]
        module=data['io_modules'][0]
        changes=[{'collection':'pa_values','id':pa['id'],'op':'update','values':{'Instance value':0}},
                 {'collection':'rtu_lines','id':line['id'],'op':'update','values':{'Baud rate':19200}},
                 {'collection':'io_modules','id':module['id'],'op':'update','values':{'Description':'Reviewed rack description'}}]
        body={'revision':data['revision'],'changes':changes}
        assert client.post('/api/v2/configuration/preview',json=body).status_code==403
        first=client.post('/api/v2/configuration/preview',headers=headers,json=body).json()
        assert first.get('token') and not first['errors'],first
        stale=client.post('/api/v2/configuration/preview',headers=headers,json=body).json()
        saved=client.post('/api/v2/configuration/apply',headers=headers,json={'token':first['token']})
        assert saved.status_code==200,saved.text
        assert client.post('/api/v2/configuration/apply',headers=headers,json={'token':first['token']}).status_code==404
        assert client.post('/api/v2/configuration/apply',headers=headers,json={'token':stale['token']}).status_code==409
        after=client.get('/api/v2/configuration').json()
        assert next(r for r in after['pa_values'] if r['id']==pa['id'])['Instance value']==0
        assert after['rtu_lines'][0]['baud_rate']==19200
        with app.state.session_factory() as session:
            state=store.load_project(session)
        assert not engine.validate_project(state)['errors']
        rack=engine._objects(state['workbooks']['IO_PHYSICAL.xlsx'],'RACK')
        assert rack[0]['Description']=='Reviewed rack description'
        before=copy.deepcopy(state['exchange_functions_detail'])
        for filename in ('PARAMETERS_PA.xlsx','MODBUS_TCP.xlsx','MODBUS_RTU.xlsx','IO_PHYSICAL.xlsx'):
            blob=engine.export_workbook(state,filename)
            state=engine.import_workbook(blob,filename,state,mode='replace')['state']
        assert not engine.validate_project(state)['errors']
        assert state['exchange_functions_detail']==before

        from backend import v2_plc, v2_plc_validation
        objects=v2_plc.build_program(state)
        for mode in ('full','partial'):
            plan=v2_plc_validation.plan_export(state,objects,mode=mode,focus='instance:DIP1500_1' if mode=='partial' else None)
            assert not plan['errors'],plan['errors']
            xml=v2_plc.serialize_program(objects,state,[r['name'] for r in plan['included']])
            assert not v2_plc_validation.validate_xml(xml,state,objects)['errors']
            assert b'<Application' not in xml
