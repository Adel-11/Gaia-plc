from io import BytesIO
from zipfile import ZipFile

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
    <data name="http://www.3s-software.com/plcopenxml/objectid" handleUnknown="discard"><ObjectId>065a9181-4d3c-489e-b33e-e582951e07b2</ObjectId></data>
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


def test_rack_mapping_roundtrip_with_full_and_partial_program_exports(rack_client):
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
    assert preview['rack'] == 'rack.xml'
    full = etree.fromstring(program.content)
    assert full.xpath('//*[local-name()="pou"]/@name')
    assert full.xpath('string(.//Parameter[@ParameterId="5000"]/Value)') == 'Preserve module settings'
    assert full.xpath('.//Mapping/Element/Element/text()') == [first['variable']]
    assert full.xpath('.//Mapping/Element/Element/@name') == ['Bit1']
    partial = client.post('/api/v2/plc/preview', json={'mode': 'partial', 'focus': 'object:GVL_IOMAP', 'selected': ['GVL_IOMAP']}, headers=headers).json()
    assert not partial['errors'], partial
    downloaded = client.get(f'/api/v2/plc/download/{partial["token"]}')
    assert downloaded.status_code == 200
    assert not etree.fromstring(downloaded.content).xpath('//*[local-name()="Device"]')

    duplicate = RACK.replace(b'</Element>\n        </Element></Mapping>',
                             b'</Element><Element name="Bit1">Application.GVL_IOMAP.IOMAP_DI_FAULT_DOSENSOR_1</Element>\n        </Element></Mapping>')
    revision = client.get('/api/v2/io-hardware').json()['revision']
    uploaded = client.post('/api/v2/io-hardware/template', files={'file': ('rack.xml', duplicate)},
                           data={'revision': revision}, headers=headers)
    assert uploaded.status_code == 200
    invalid = client.post('/api/v2/plc/preview', json={'mode': 'full'}, headers=headers).json()
    assert any('Corrigez le mappage' in error for error in invalid['errors'])


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


def test_real_gaia_cards_create_move_unlink_and_export_scoped_xml(rack_client):
    client, headers = rack_client

    def post(path, expected=200, **payload):
        view = client.get('/api/v2/io-hardware').json()
        response = client.post('/api/v2/io-hardware' + path, headers=headers, json={'revision': view['revision'], **payload})
        assert response.status_code == expected, response.text
        return response

    loaded = post('/template/gaia').json()
    hardware = loaded['hardware']
    assert [(row['slot'], row['model']) for row in hardware['cards']] == [(1, 'P2-16AD-1'), (2, 'P2-16DA-1'), (3, 'P2-32TD2P'), (4, 'P2-32ND3-1')]
    assert len(hardware['channels']) == 96
    post('/template/gaia', expected=409)
    analog_in = [row for row in hardware['channels'] if row['module'] == 'Module']
    analog_out = next(row for row in hardware['channels'] if row['module'] == 'Module_3')
    assert analog_out['direction'] == 'output' and analog_out['iec_type'] == 'DWORD'
    assert not any(v['iec_type'] == 'DWORD' and v['direction'] != 'input' for v in hardware['variables'])

    old, new = analog_in[:2]
    signal = old['variable'].split('.')[-1]
    moved = post('/mappings', changes={new['key']: signal, old['key']: None}).json()
    channels = {row['key']: row for row in moved['hardware']['channels']}
    assert not channels[old['key']]['variable'] and channels[new['key']]['variable'] == old['variable']

    created = post('/channels/create-variable', channel_key=analog_out['key'], signal='QA_Consigne').json()
    name = 'IOMAP_AO_QA_Consigne'
    assert next(row for row in created['variables'] if row['name'] == name)['iec_type'] == 'DWORD'
    assert next(row for row in created['hardware']['channels'] if row['key'] == analog_out['key'])['variable'].endswith(name)
    post('/channels/create-variable', expected=422, channel_key=analog_out['key'], signal='Another')

    response = post('/exports/bundle')
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ['Gaia-GVL-ES.xml', 'Gaia-GFC-ES.xml', 'Gaia-mapping-ES.xml']
        gvl = etree.fromstring(archive.read('Gaia-GVL-ES.xml'))
        gfc_bytes = archive.read('Gaia-GFC-ES.xml')
        gfc = etree.fromstring(gfc_bytes)
        rack, channels = read_hardware(archive.read('Gaia-mapping-ES.xml'))
    assert set(gvl.xpath('//*[local-name()="globalVars"]/@name')) == {'GVL_IO', 'GVL_IOMAP'}
    assert not gvl.xpath('//*[local-name()="Device" or local-name()="pou"]')
    assert name in gvl.xpath('//*[local-name()="variable"]/@name')
    assert set(gfc.xpath('//*[local-name()="pou"]/@name')) == {'GFC_IO_IN', 'GFC_IO_OUT'}
    assert not gfc.xpath('//*[local-name()="Device" or local-name()="globalVars"]')
    assert channels[analog_out['key']].variable.endswith(name) and channels[new['key']].variable.endswith(signal)
    assert not rack.xpath('//*[local-name()="pou" or local-name()="globalVars"]')

    preview = client.post('/api/v2/plc/preview', json={'mode': 'full'}, headers=headers).json()
    assert not preview['errors'] and preview['rack'], preview
    program = client.get(f'/api/v2/plc/download/{preview["token"]}')
    assert program.status_code == 200, program.text
    full = etree.fromstring(program.content)
    for expression in ('//*[local-name()="configuration"]/@name', '//*[local-name()="ProjectStructure"]/*[@Name="P2_11B"]//@ObjectId', './/Mapping//Element[not(Element)]/text()'):
        assert full.xpath(expression) == rack.xpath(expression)

    unlinked = post('/mappings', changes={analog_out['key']: None}).json()
    assert name in [row['name'] for row in unlinked['variables']]
    assert not next(row for row in unlinked['hardware']['channels'] if row['key'] == analog_out['key'])['variable']
    assert name.encode() in post('/exports/gvl').content
    assert post('/exports/gfc').content == gfc_bytes
