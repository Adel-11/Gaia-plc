from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app
from backend.v2_io_hardware import read_hardware
from backend.v2_plc import PLC


RACK = f'''<project xmlns="{PLC}"><instances><configurations>
  <configuration name="Module_2"><addData>
    <data name="Device" handleUnknown="discard"><Device xmlns=""><DeviceType>
      <Connector connectorId="2"><HostParameterSet>
        <Parameter ParameterId="3001"><Attributes channel="input"/><Value>
          <Element name="Byte0"><Element name="Bit0" visiblename="Ch1">0</Element><Element name="Bit1" visiblename="Ch2">0</Element></Element>
        </Value><Name>Channels</Name><Mapping><Element name="Byte0">
          <Element name="Bit0">Application.GVL_IOMAP.IOMAP_DI_FAULT_DOSENSOR_1</Element>
        </Element></Mapping></Parameter>
        <Parameter ParameterId="5000"><Value>Preserve module settings</Value></Parameter>
      </HostParameterSet></Connector>
    </DeviceType></Device></data>
    <data name="http://www.3s-software.com/plcopenxml/objectid"><ObjectId>065a9181-4d3c-489e-b33e-e582951e07b2</ObjectId></data>
  </addData></configuration>
</configurations></instances></project>'''.encode()


def test_rack_mapping_roundtrip_is_separate_from_program_export(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('GAIA_UPGRADE_HMI_PROJECT', 'Gaia')
    monkeypatch.setenv('ADMIN_USERNAME', 'rack-test')
    monkeypatch.setenv('ADMIN_PASSWORD', 'rack-test-password')
    app = create_app(data_dir=tmp_path, secret_key='rack-test-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post('/api/login', json={'username': 'rack-test', 'password': 'rack-test-password'})
        headers = {'X-CSRF-Token': login.json()['csrf_token']}
        initial = client.get('/api/v2/io-hardware').json()
        assert initial['hardware'] is None
        loaded = client.post('/api/v2/io-hardware/template', files={'file': ('rack.xml', RACK)},
                             data={'revision': initial['revision']}, headers=headers)
        assert loaded.status_code == 200, loaded.text
        view = loaded.json()
        first, second = view['hardware']['channels']
        assert first['iec_type'] == 'BOOL' and first['variable'].endswith('IOMAP_DI_FAULT_DOSENSOR_1')
        update = {'revision': view['revision'], 'changes': {second['key']: 'IOMAP_DI_FAULT_DOSENSOR_1'}}
        assert client.post('/api/v2/io-hardware/mappings', json=update, headers=headers).status_code == 422
        update['changes'][first['key']] = None
        saved = client.post('/api/v2/io-hardware/mappings', json=update, headers=headers)
        assert saved.status_code == 200, saved.text
        assert client.post('/api/v2/io-hardware/mappings', json=update, headers=headers).status_code == 409

        response = client.get('/api/v2/io-hardware/export')
        assert response.status_code == 200, response.text
        root, channels = read_hardware(response.content)
        assert channels[first['key']].variable == ''
        assert channels[second['key']].variable == first['variable']
        assert root.find('.//Parameter[@ParameterId="5000"]/Value').text == 'Preserve module settings'
        preview = client.post('/api/v2/plc/preview', json={'mode': 'full'}, headers=headers).json()
        assert not preview['errors'], preview
        program = client.get(f'/api/v2/plc/download/{preview["token"]}')
        assert program.status_code == 200, program.text
        assert not etree.fromstring(program.content).xpath('//*[local-name()="Device"]')
