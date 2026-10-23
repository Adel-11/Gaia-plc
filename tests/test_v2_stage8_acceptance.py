"""Stage 8 code edits survive model updates, workbooks and PLCopen export."""
from fastapi.testclient import TestClient
from lxml import etree


def test_code_edits_roundtrip_and_full_partial_exports(tmp_path,monkeypatch):
    from backend.main import create_app
    from backend import v2_store as store,v2_engine as engine
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','stage8-admin')
    monkeypatch.setenv('ADMIN_PASSWORD','stage8-local-tests')
    app=create_app(data_dir=tmp_path,secret_key='stage8-tests-'*4,cookie_secure=False)
    custom='// Stage 8 preserved custom section\r\nGVL_IO.IO_DOSENSOR_1.INPUT_SIGNAL := GVL_IO.IO_DOSENSOR_1.INPUT_SIGNAL;'
    with TestClient(app) as client:
        login=client.post('/api/login',json={'username':'stage8-admin','password':'stage8-local-tests'}).json()
        headers={'X-CSRF-Token':login['csrf_token']}
        def catalog():
            r=client.get('/api/v2/code');assert r.status_code==200,r.text
            return r.json()
        def save(name,**changes):
            p=client.post('/api/v2/code/preview',headers=headers,json={'revision':catalog()['revision'],'name':name,**changes}).json()
            assert not p['errors'] and p.get('token'),p
            a=client.post('/api/v2/code/apply',headers=headers,json={'token':p['token']});assert a.status_code==200,a.text
        initial=catalog()
        assert len([o for o in initial['objects'] if o['category']=='generated'])==8
        fn=next(o for o in initial['objects'] if o['name']=='FNC_WORDS_TO_REAL')
        body=fn['body'].replace(':= Conversion.Real_value;',':= Conversion.Real_value + 0.0;')
        assert body!=fn['body']
        save(fn['name'],body=body,custom_code='// Utility custom section preserved')
        save('PLC_PRG',custom_code=custom)
        after=catalog()
        assert next(o for o in after['objects'] if o['name']=='PLC_PRG')['custom_code']==custom
        assert next(o for o in after['objects'] if o['name']==fn['name'])['changed']
        # Trigger regeneration via another page and confirm authored sections are retained.
        config=client.get('/api/v2/configuration').json();pa=config['pa_values'][0]
        p=client.post('/api/v2/configuration/preview',headers=headers,json={'revision':config['revision'],'changes':[{'collection':'pa_values','id':pa['id'],'op':'update','values':{'Instance value':False}}]}).json()
        assert not p['errors'],p
        assert client.post('/api/v2/configuration/apply',headers=headers,json={'token':p['token']}).status_code==200
        with app.state.session_factory() as session: state=store.load_project(session)
        for filename in ('PARAMETERS_PA.xlsx','MODBUS_TCP.xlsx'):
            result=engine.import_workbook(engine.export_workbook(state,filename),filename,state,mode='replace')
            assert not result['validation']['errors'],result['validation']
            state=result['state']
        assert state['generated_custom_code']['PLC_PRG']==custom
        assert next(f for f in state['functions'] if f['name']==fn['name'])['body']==body
        for mode in ('full','partial'):
            planned=client.post('/api/v2/plc/preview',headers=headers,json={'mode':mode,**({'focus':'object:PLC_PRG'} if mode=='partial' else {})}).json()
            assert not planned['errors'],planned
            xml=client.get('/api/v2/plc/download/'+planned['token'])
            assert xml.status_code==200,xml.text
            root=etree.fromstring(xml.content)
            st=root.xpath('string(//*[local-name()="pou"][@name="PLC_PRG"]/*[local-name()="body"]/*[local-name()="ST"]/*[local-name()="xhtml"])')
            assert custom in st
            assert not root.xpath('//*[local-name()="resource" or local-name()="task"]')
