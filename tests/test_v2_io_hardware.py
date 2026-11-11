from fastapi.testclient import TestClient
from lxml import etree
import pytest

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


@pytest.fixture
def rack_client(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('GAIA_UPGRADE_HMI_PROJECT', 'Gaia')
    monkeypatch.setenv('ADMIN_USERNAME', 'rack-test')
    monkeypatch.setenv('ADMIN_PASSWORD', 'rack-test-password')
    app = create_app(data_dir=tmp_path, secret_key='rack-test-' * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post('/api/login', json={'username': 'rack-test', 'password': 'rack-test-password'})
        headers = {'X-CSRF-Token': login.json()['csrf_token']}
        yield client, headers


def test_rack_mapping_roundtrip_is_separate_from_program_export(rack_client):
    client, headers = rack_client
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


def test_delete_and_reassign_legacy_iomap_updates_program_and_rack(rack_client):
    client, headers = rack_client
    variable = 'IOMAP_DI_FAULT_DOSENSOR_1'
    analog = 'IOMAP_AI_SIGNAL_DOSENSOR_1'
    statement = f'GVL_IO.IO_DOSENSOR_1.INPUT_SENSOR_FAULT := GVL_IOMAP.{variable};'

    def get_view():
        return client.get('/api/v2/io-hardware').json()

    def change(action, name=variable, expected=200, **kwargs):
        response = client.post('/api/v2/io-hardware/copies', headers=headers, json={
            'revision': get_view()['revision'], 'action': action, 'iomap': name, **kwargs})
        assert response.status_code == expected, response.text
        return response.json()

    def code(name):
        return client.get(f'/api/v2/code/source/{name}').json()

    initial = get_view()
    assert initial['hardware'] is None and variable in [row['name'] for row in initial['variables']]
    instance = next(row for row in initial['instances'] if row['name'] == 'DOSENSOR_1')
    assert any(member['name'] == 'INPUT_SENSOR_FAULT' and member['direction'] == 'input' for member in instance['members'])
    assert statement in code('GFC_IO_IN')['body']
    rack = RACK.replace(b'<Parameter ParameterId="5000">', b'''<Parameter ParameterId="4001"><Attributes channel="output"/><Value>
        <Element name="Byte0"><Element name="Bit0" visiblename="Ch1">0</Element></Element>
        </Value><Mapping><Element name="Byte0"><Element name="Bit0">Application.GVL_IOMAP.IOMAP_DO_HIGH_DOSENSOR_1</Element></Element></Mapping>
        </Parameter><Parameter ParameterId="5000">''')
    uploaded = client.post('/api/v2/io-hardware/template', files={'file': ('rack.xml', rack)},
                           data={'revision': initial['revision']}, headers=headers)
    assert uploaded.status_code == 200, uploaded.text
    first, second, unrelated = uploaded.json()['hardware']['channels']

    # Removing just the copy leaves both the declaration and physical channel intact.
    disconnected = change('disconnect')
    assert disconnected['hardware']['channels'][0]['variable'] == first['variable']
    assert variable in code('GVL_IOMAP')['declaration']
    assert variable not in code('GFC_IO_IN')['body']
    payload = {'instance': 'DOSENSOR_1', 'member': 'INPUT_SENSOR_FAULT', 'direction': 'input'}
    assert 'même type IEC' in change('assign', analog, expected=422, **payload)['detail']
    assert 'ne peut pas être partagée' in change('assign', expected=422, channel_key=second['key'], **payload)['detail']
    assert variable not in code('GFC_IO_IN')['body']

    # Assignment opts the selected instance back into the generated input block.
    review = client.post('/api/v2/code/preview', headers=headers, json={
        'revision': get_view()['revision'], 'name': 'GFC_IO_IN', 'included_instances': []}).json()
    assert review.get('token'), review
    assert client.post('/api/v2/code/apply', headers=headers, json={'token': review['token']}).status_code == 200
    assigned = change('assign', channel_key=first['key'], **payload)
    assert any(row['st'] == statement and row['generated'] for row in assigned['copies'])
    assert statement in code('GFC_IO_IN')['body']

    # A reference in handwritten ST blocks removal without touching either XML.
    main = code('PLC_PRG')['body']
    manual = client.post('/api/v2/code/source/PLC_PRG', headers=headers, json={
        'revision': get_view()['revision'], 'body': main + '\n' + statement})
    assert manual.status_code == 200, manual.text
    assert 'PLC_PRG' in change('delete', expected=422)['detail']
    assert get_view()['hardware']['channels'][0]['variable'] == first['variable']
    reset = client.post('/api/v2/code/source/PLC_PRG', headers=headers, json={
        'revision': get_view()['revision'], 'body': None})
    assert reset.status_code == 200, reset.text

    deleted = change('delete')
    assert variable not in [row['name'] for row in deleted['variables']]
    assert not deleted['hardware']['channels'][0]['variable']
    assert deleted['hardware']['channels'][2]['variable'] == unrelated['variable']
    assert variable not in code('GVL_IOMAP')['declaration']
    assert variable not in code('GFC_IO_IN')['body']
    assert analog in code('GFC_IO_IN')['body']
    assert 'IOMAP_DO_HIGH_DOSENSOR_1 := GVL_IO.' in code('GFC_IO_OUT')['body']
    exported = client.get('/api/v2/io-hardware/export')
    assert exported.status_code == 200, exported.text
    assert variable.encode() not in exported.content and unrelated['variable'].encode() in exported.content
    program = client.post('/api/v2/plc/preview', json={'mode': 'full'}, headers=headers).json()
    assert not program['errors'], program
    xml = client.get(f'/api/v2/plc/download/{program["token"]}')
    assert xml.status_code == 200 and variable.encode() not in xml.content
