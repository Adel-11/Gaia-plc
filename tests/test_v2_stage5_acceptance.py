"""Stage 5 acceptance across type wizard, FB wiring, instance settings and Excel/XML."""
import io
from fastapi.testclient import TestClient
from backend.main import create_app


def test_new_type_flow_instance_parameters_and_workbooks_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.setenv('ADMIN_USERNAME','stage5-admin');monkeypatch.setenv('ADMIN_PASSWORD','stage5-password')
    app=create_app(data_dir=tmp_path,secret_key='stage5-tests-'*4,cookie_secure=False)
    with TestClient(app) as client:
        login=client.post('/api/login',json={'username':'stage5-admin','password':'stage5-password'}).json()
        headers={'X-CSRF-Token':login['csrf_token']}
        project=client.get('/api/v2/project').json()
        def member(name,iec,default=None):return {'name':name,'iec_type':iec,'default_value':default}
        payload={'revision':project['revision'],'name':'PumpUnit','kind':'Equipment','communication':'Wired I/O',
            'duts':[{'role':'COM','members':[member('SCADA_INPUT_RUN','BOOL'),member('HMI_INPUT_RUN','BOOL'),member('SCADA_OUTPUT_ACTIVE','BOOL')]},
                    {'role':'EXC','members':[member('SPARE','BOOL')]},{'role':'INT','members':[member('RUN','BOOL'),member('ACTIVE','BOOL')]},
                    {'role':'IO','members':[member('INPUT_SENSOR','BOOL')]},{'role':'PA','members':[member('LIMIT','WORD',7)]}],
            'supervision':[{'member_name':'COM.SCADA_INPUT_RUN'},{'member_name':'COM.HMI_INPUT_RUN'},{'member_name':'COM.SCADA_OUTPUT_ACTIVE'}],
            'processing_code':'INTERN.ACTIVE := INTERN.RUN;\n'}
        p=client.post('/api/v2/types/preview',headers=headers,json=payload).json()
        assert p['errors']==[],p
        created=client.post('/api/v2/types/apply',headers=headers,json={'token':p['token']})
        assert created.status_code==200,created.text
        obj=client.get('/api/v2/objects/type/PumpUnit').json()
        assert obj['fb']['name']=='FB_INST_PumpUnit'
        wires=[{'section':'inputs','source':'COM.SCADA_INPUT_RUN','destination':'INTERN.RUN'},
               {'section':'outputs','source':'INTERN.ACTIVE','destination':'COM.SCADA_OUTPUT_ACTIVE'}]
        p=client.post('/api/v2/objects/type/PumpUnit/preview',headers=headers,json={'revision':obj['revision'],'mappings':wires}).json()
        assert p['errors']==[],p
        assert client.post('/api/v2/objects/apply',headers=headers,json={'token':p['token']}).status_code==200
        revision=client.get('/api/v2/project').json()['revision']
        p=client.post('/api/v2/instances/preview',headers=headers,json={'revision':revision,'action':'create','name':'PumpUnit_1','type_id':'PumpUnit'}).json()
        assert p['errors']==[],p
        assert len(p['configuration']['tcp_points'])==3
        assert client.post('/api/v2/instances/apply',headers=headers,json={'token':p['token']}).status_code==200
        config=client.get('/api/v2/instances/PumpUnit_1/configuration').json()
        assert config['pa_values'][0]['Member']=='LIMIT'
        p=client.post('/api/v2/instances/preview',headers=headers,json={'revision':config['revision'],'action':'pa','instance_id':'PumpUnit_1','values':{'LIMIT':42}}).json()
        assert p['errors']==[],p
        assert client.post('/api/v2/instances/apply',headers=headers,json={'token':p['token']}).status_code==200
        workbook=client.get('/api/v2/exports/TYPE_PumpUnit.xlsx')
        assert workbook.status_code==200
        preview=client.post('/api/v2/imports/preview',headers=headers,files={'file':('TYPE_PumpUnit.xlsx',workbook.content)},data={'mode':'merge'}).json()
        assert preview['validation']['errors']==[],preview
        assert client.post('/api/v2/imports/apply',headers=headers,json={'token':preview['token']}).status_code==200
        config=client.get('/api/v2/instances/PumpUnit_1/configuration').json()
        assert any(str(r.get('value'))=='42' for r in config['pa_values'])
        plan=client.post('/api/v2/plc/preview',headers=headers,json={'mode':'full'}).json()
        assert plan['errors']==[],plan['errors']
        xml=client.get('/api/v2/plc/download/'+plan['token']).content
        assert b'INTERN.RUN := COM.SCADA_INPUT_RUN;' in xml
        assert b'FB_INST_PumpUnit' in xml and b'COM_PumpUnit_1' in xml
