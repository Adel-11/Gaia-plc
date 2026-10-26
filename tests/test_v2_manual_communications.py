import copy
import pytest
from backend import v2_engine, v2_plc
from backend.v2_instance_editor import _add_instance
from backend.v2_configuration import apply_changes, synchronize_workbooks
from backend.v2_manual_comm import create_point, members_for


def text(objects, name):
    return objects[name]['element'].xpath('string(./p:body/p:ST/h:xhtml)', namespaces={'p': v2_plc.PLC, 'h': 'http://www.w3.org/1999/xhtml'})


def test_explicit_tcp_configuration_does_not_add_an_instance_to_gfc():
    state=copy.deepcopy(v2_engine.load_pack())
    typ=next(t for t in state['types'] if t['name']=='DOSENSOR')
    _add_instance(state,'NEW_SENSOR',typ)
    member=next(m for m in members_for(state,state['instances'][-1]) if m['iec_type']=='BOOL')
    values={'Instance':'NEW_SENSOR','Member':member['path'],'Client':'FUXA','Table':'coils','Modbus address':900,'Direction':'Client to PLC'}
    apply_changes(state,[{'collection':'tcp_points','op':'create','values':values}])
    synchronize_workbooks(state)
    row=state['tcp_points'][-1]
    assert row['GVL_COM variable'].startswith('GVL_COM.COM_NEW_SENSOR.')
    assert not [e for e in v2_engine.validate_project(state)['errors'] if 'NEW_SENSOR' in e]
    objects=v2_plc.build_program(state)
    assert 'NEW_SENSOR' not in text(objects,'GFC_COM_IN')
    assert 'FB_NEW_SENSOR(' not in text(objects,'GFB_INST')
    state['program_membership']['GFC_COM_IN'].append('NEW_SENSOR')
    assert 'COM_NEW_SENSOR.' in text(v2_plc.build_program(state),'GFC_COM_IN')
    with pytest.raises(ValueError,match='already used'):
        apply_changes(state,[{'collection':'tcp_points','op':'create','values':values}])


def test_rtu_point_is_explicit_and_preserves_exact_word_leaf():
    state=copy.deepcopy(v2_engine.load_pack())
    typ=next(t for t in state['types'] if t['name']=='ADM100W')
    _add_instance(state,'NEW_METER',typ)
    member=next(m for m in members_for(state,state['instances'][-1]) if m['iec_type']=='WORD' and '[0]' in m['path'])
    values={'Instance':'NEW_METER','Member':member['path'],'Table':'IR','Equipment address':0,'Direction':'Equipment to PLC','GVL_MODBUS variable':'GVL_MODBUS.RTU_NEW_METER_WORD'}
    with pytest.raises(ValueError,match='slave first'):
        create_point(state,'rtu_channels',values)
    apply_changes(state,[{'collection':'rtu_slaves','op':'create','values':{'Instance':'NEW_METER','Line':'RTU_1','Slave address':200,'CODESYS slave device':'Manual'}}])
    create_point(state,'rtu_channels',values)
    synchronize_workbooks(state)
    assert any(s['name']=='NEW_METER' for s in state['workbooks']['MODBUS_RTU.xlsx']['sheets'])
    assert 'RTU_NEW_METER_WORD' not in text(v2_plc.build_program(state),'GFC_COM_IN')
    state['program_membership']['GFC_COM_IN'].append('NEW_METER')
    objects=v2_plc.build_program(state)
    assert f"GVL_COM.COM_NEW_METER.{member['path'].removeprefix('COM.')} := GVL_MODBUS.RTU_NEW_METER_WORD;" in text(objects,'GFC_COM_IN')
    var=objects['GVL_MODBUS']['element'].find(f"{v2_plc.NS}variable[@name='RTU_NEW_METER_WORD']/{v2_plc.NS}type/{v2_plc.NS}WORD")
    assert var is not None
    assert not [e for e in v2_engine.validate_project(state)['errors'] if 'NEW_METER' in e]
    updated=v2_engine.import_workbook(v2_engine.export_workbook(state,'MODBUS_RTU.xlsx'),'MODBUS_RTU.xlsx',state)['state']
    channel=next(r for r in updated['rtu_channels'] if r.get('instance_id')=='NEW_METER')
    assert channel['manual_scalar']
    assert f"GVL_COM.COM_NEW_METER.{member['path'].removeprefix('COM.')} := GVL_MODBUS.RTU_NEW_METER_WORD;" in text(v2_plc.build_program(updated),'GFC_COM_IN')


def test_no_communication_without_an_instance():
    state=copy.deepcopy(v2_engine.load_pack())
    with pytest.raises(ValueError,match='Declare an instance'):
        create_point(state,'tcp_points',{'Instance':'NOT_DECLARED'})
