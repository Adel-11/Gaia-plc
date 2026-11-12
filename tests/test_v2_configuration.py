"""Focused checks for reviewed V2 configuration edits."""
import copy

import pytest

from backend.v2_configuration import apply_changes, synchronize_workbooks, validate_configuration
from backend.v2_engine import export_workbook, load_pack, workbook_to_data, _objects
from backend.v2_addressing import allocate_tcp_points, client_ranges, free_addresses, set_client_ranges


def test_excel_export_uses_current_modbus_rows_even_with_stale_workbook():
    state = copy.deepcopy(load_pack())
    tcp_file = 'MODBUS_TCP.xlsx'
    removed = state['tcp_points'].pop(0)
    edited = next(row for row in state['tcp_points'] if row['sheet'] == removed['sheet'])
    edited['Name'] = 'Updated TCP point'
    added = {**edited, 'Modbus address': 900, 'Name': 'New TCP point'}
    state['tcp_points'].append(added)
    exported = workbook_to_data(export_workbook(state, tcp_file), tcp_file)
    by_address = {(row['Client'], row['Modbus address']): row['Name'] for row in _objects(exported, edited['sheet'])}
    assert (removed['Client'], removed['Modbus address']) not in by_address
    assert by_address[edited['Client'], edited['Modbus address']] == 'Updated TCP point'
    assert by_address[added['Client'], 900] == 'New TCP point'

    rtu_file = 'MODBUS_RTU.xlsx'
    stored = state['workbooks'][rtu_file]
    stored['sheets'] = [sheet for sheet in stored['sheets'] if sheet['name'] in {'README', 'LINES', 'SLAVES', 'LISTS'}]
    state['rtu_channels'] = [{'instance_id': 'QA_RTU', 'table': 'HR', 'address': 42, 'name': 'New RTU point'}]
    exported = workbook_to_data(export_workbook(state, rtu_file), rtu_file)
    point, = _objects(exported, 'QA_RTU')
    assert (point['Table'], point['Equipment address'], point['Name']) == ('HR', 42, 'New RTU point')
    assert all(sheet['name'] != 'QA_RTU' for sheet in stored['sheets'])


@pytest.mark.parametrize("collection,table,direction,member_type", [
    ("tcp_points", "holding_registers", "Client to PLC", "REAL"),
    ("tcp_points", "input_registers", "PLC to client", "REAL"),
    ("rtu_channels", "IR", "Equipment to PLC", "DINT"),
])
def test_modbus_32_bit_pair_generation_workbook_and_atomic_edit(collection, table, direction, member_type):
    from backend.v2_codesys import _st
    from backend.v2_hmi import load_hmi_pack
    from backend.v2_manual_comm import pair_key
    from backend.v2_plc import build_program, serialize_program
    from backend.v2_plc_validation import validate_xml

    state = copy.deepcopy(load_hmi_pack())
    typ = next(row for row in state["types"] if row["name"] == "DIP1500")
    com = next(dut for dut in typ["duts"] if dut["role"] == "COM")
    com["members"].append({"name": "HMI_INPUT_QA32", "iec_type": member_type})
    tcp = collection == "tcp_points"
    inbound = direction.endswith("to PLC")
    field = "Modbus address" if tcp else "Equipment address"
    filename = "MODBUS_TCP.xlsx" if tcp else "MODBUS_RTU.xlsx"
    values = {"Instance": "DIP1500_1", "Member": "HMI_INPUT_QA32", "Table": table,
              "Direction": direction, field: 700, "Register word order": "MSW_FIRST"}
    values.update({"Client": "HMI"} if tcp else {"GVL_MODBUS variable": "GVL_MODBUS.QA_RTU_32"})
    add = {"collection": collection, "op": "create", "values": values}
    apply_changes(state, [add])
    low_address, high_address = [row for row in state[collection] if pair_key(row)]
    assert [(row[field], row["Word"]) for row in (low_address, high_address)] == [(700, "MSW"), (701, "LSW")]

    def generated():
        objects = build_program(state)
        xml = serialize_program(objects, state, list(objects))
        assert not validate_xml(xml, state, objects)["errors"]
        return _st(objects["GFC_COM_IN" if inbound else "GFC_COM_OUT"]["element"]), xml

    body, xml = generated()
    assert f"GaiaModbusAs{member_type} := ADR(GaiaModbusBits);" in body
    member = "GVL_COM.COM_DIP1500_1.HMI_INPUT_QA32"
    pointer = f"GaiaModbusAs{member_type}^"
    assert (f"{member} := {pointer};" if inbound else f"{pointer} := {member};") in body
    if not tcp:
        assert b'<variable name="QA_RTU_32_1">' in xml and b'<variable name="QA_RTU_32_2">' in xml
    if inbound:
        assert ("Holding_register_TCP_HMI[699]" if tcp else "QA_RTU_32_1") in body.split("SHL(", 1)[1].split(", 16)", 1)[0]
    else:
        assert "Input_register_TCP_HMI[699] := DWORD_TO_WORD(SHR(GaiaModbusBits, 16));" in body
    with pytest.raises(ValueError, match="libres"):
        apply_changes(state, [{**add, "values": {**values, field: 701, **({} if tcp else {"GVL_MODBUS variable": "GVL_MODBUS.QA_RTU_OVERLAP"})}}])
    update = {"collection": collection, "op": "update", "id": low_address["id"]}
    apply_changes(state, [{**update, "values": {"Register word order": "LSW_FIRST"}}])
    assert (low_address["Word"], high_address["Word"]) == ("LSW", "MSW")
    body, _ = generated()
    if inbound:
        assert ("Holding_register_TCP_HMI[700]" if tcp else "QA_RTU_32_2") in body.split("SHL(", 1)[1].split(", 16)", 1)[0]
    else:
        assert "Input_register_TCP_HMI[700] := DWORD_TO_WORD(SHR(GaiaModbusBits, 16));" in body
    workbook = workbook_to_data(export_workbook(state, filename), filename)
    rows = _objects(workbook, table.upper() if tcp else "DIP1500_1")
    assert [(row[field], row["Word"]) for row in rows if row["Name"] == "DIP1500_1.HMI_INPUT_QA32"] == [(700, "LSW"), (701, "MSW")]
    from backend.v2_engine import _excel_state
    rebuilt = {}
    _excel_state(rebuilt, workbook)
    assert [row["Word"] for row in rebuilt[collection] if pair_key(row)] == ["LSW", "MSW"]
    apply_changes(state, [{"collection": collection, "op": "delete", "id": high_address["id"]}])
    assert not any(pair_key(row) for row in state[collection])


def test_legacy_tcp_union_can_swap_words_without_changing_either_address():
    from backend.v2_hmi import load_hmi_pack
    from backend.v2_manual_comm import editable_pair_key, pair_rows

    state = copy.deepcopy(load_hmi_pack())
    seed = next(row for row in state["tcp_points"] if editable_pair_key(row))
    first, second = sorted(pair_rows(state["tcp_points"], seed), key=lambda row: row["address"])
    addresses = [row["address"] for row in (first, second)]
    order = "LSW_FIRST" if first["Word"] == "MSW" else "MSW_FIRST"
    apply_changes(state, [{"collection": "tcp_points", "op": "update", "id": first["id"],
                           "values": {"Register word order": order}}])
    assert [row["address"] for row in (first, second)] == addresses
    for row in (first, second):
        assert row["GVL_COM variable"].endswith(".Word_value[0]" if row["Word"] == "LSW" else ".Word_value[1]")
    assert first["Word"] == order.split("_")[0]


def test_pa_zero_is_a_valid_value_and_blank_remains_inheritance():
    state = copy.deepcopy(load_pack())
    row = next(r for r in state["pa_values"] if str(r.get("Type", "")).upper() in {"WORD", "INT", "REAL", "BOOL"})
    warnings, _ = apply_changes(state, [{"collection":"pa_values","id":row["id"],"op":"update","values":{"Instance value":0}}])
    assert row["Instance value"] == 0
    assert not warnings
    apply_changes(state, [{"collection":"pa_values","id":row["id"],"op":"update","values":{"Instance value":""}}])
    assert row["Instance value"] == ""


def test_tcp_global_occupancy_and_readonly_tcp_columns():
    state = copy.deepcopy(load_pack())
    point = state["tcp_points"][0]
    other = next(p for p in state["tcp_points"] if p is not point and p.get("table") == point.get("table"))
    with pytest.raises(ValueError, match="already used"):
        apply_changes(state, [{"collection":"tcp_points","id":point["id"],"op":"update","values":{"Modbus address":other["Modbus address"]}}])
    with pytest.raises(ValueError, match="read-only"):
        apply_changes(state, [{"collection":"tcp_points","id":point["id"],"op":"update","values":{"Word":9}}])


def test_rtu_duplicate_slave_address_and_line_settings_validation():
    state = copy.deepcopy(load_pack())
    slaves = [s for s in state["rtu_slaves"] if str(s.get("Slave address", "")).isdigit()]
    if len(slaves) >= 2:
        a, b = slaves[:2]
        if a.get("Line") == b.get("Line"):
            with pytest.raises(ValueError, match="already used"):
                apply_changes(state, [{"collection":"rtu_slaves","id":b["id"],"op":"update","values":{"Slave address":a["Slave address"]}}])
    line = state["rtu_lines"][0]
    with pytest.raises(ValueError, match="Baud rate"):
        apply_changes(state, [{"collection":"rtu_lines","id":line["id"],"op":"update","values":{"Baud rate":12345}}])


def test_sync_keeps_unrelated_exchange_sheets_custom_rows_and_headers():
    state = copy.deepcopy(load_pack())
    book = state["workbooks"]["EXCHANGES_EXC.xlsx"]
    before = copy.deepcopy(book)
    synchronize_workbooks(state)
    assert book == before
    # A supported edit updates its workbook value while retaining its unknown column.
    pa = state["pa_values"][0]
    pa_book = state["workbooks"]["PARAMETERS_PA.xlsx"]
    sheet = next(s for s in pa_book["sheets"] if s["name"] == pa.get("instance"))
    sheet["headers"].append("Vendor note")
    sheet["rows"][0].append("keep me")
    old_note = "keep me"
    apply_changes(state, [{"collection":"pa_values","id":pa["id"],"op":"update","values":{"Instance value":0}}])
    synchronize_workbooks(state)
    assert sheet["rows"][0][-1] == old_note


def test_io_assignment_does_not_rename_physical_mapping_and_invalid_member_is_rejected():
    state = copy.deepcopy(load_pack())
    channel = next(c for c in state["io_channels"] if c.get("Instance") and c.get("GVL_IO member"))
    original_name = channel.get("Name")
    with pytest.raises(ValueError, match="existing I/O member"):
        apply_changes(state, [{"collection":"io_channels","id":channel["id"],"op":"update","values":{"GVL_IO member":"not-a-member"}}])
    assert channel.get("Name") == original_name


def test_io_existing_full_member_path_survives_edit_and_new_assignment_sets_full_path():
    state = copy.deepcopy(load_pack())
    existing = next(c for c in state["io_channels"] if c.get("Instance"))
    full_member = existing["GVL_IO member"]
    apply_changes(state, [{"collection":"io_channels","id":existing["id"],"op":"update","values":{"Signal":"updated signal","GVL_IO member":full_member}}])
    assert existing["GVL_IO member"] == full_member
    source_instance = next(i for i in state["instances"] if str(i.get("id",i.get("name")))==existing["Instance"])
    state["instances"].append({"id":"NEW_IO_TEST","name":"NEW_IO_TEST","type_id":source_instance["type_id"]})
    typ = next(t for t in state["types"] if str(t.get("id"))==str(source_instance["type_id"]) or str(t.get("name"))==str(source_instance["type_id"]))
    member = next(m for d in typ["duts"] if str(d.get("role")).upper()=="IO" for m in d["members"] if str(m.get("iec_type")).upper()=="BOOL")
    spare = next(c for c in state["io_channels"] if not c.get("Instance") and not c.get("GVL_IOMAP variable") and str(c.get("I/O type","")).upper().startswith("D"))
    leaf = member["name"]
    apply_changes(state, [{"collection":"io_channels","id":spare["id"],"op":"update","values":{"Instance":"NEW_IO_TEST","GVL_IO member":leaf,"GVL_IOMAP variable":"GVL_IOMAP.NEW_CHANNEL","IOMAP type":"BOOL"}}])
    assert spare["GVL_IO member"] == f"GVL_IO.IO_NEW_IO_TEST.{leaf}"


def test_new_empty_io_channel_can_receive_explicit_mapping_and_requires_manual_step():
    state = copy.deepcopy(load_pack())
    channel = next(c for c in state["io_channels"] if not c.get("Instance") and not c.get("GVL_IOMAP variable"))
    _, manual = apply_changes(state, [{"collection":"io_channels","id":channel["id"],"op":"update","values":{"GVL_IOMAP variable":"Manual_Input_1","IOMAP type":"DWORD"}}])
    assert channel["GVL_IOMAP variable"] == "GVL_IOMAP.Manual_Input_1"
    assert any("Manual_Input_1" in step for step in manual)
    with pytest.raises(ValueError, match="fixed for existing channels"):
        apply_changes(state, [{"collection":"io_channels","id":channel["id"],"op":"update","values":{"GVL_IOMAP variable":"Renamed_Input"}}])


def test_validation_reports_duplicate_global_tcp_addresses():
    state = copy.deepcopy(load_pack())
    point = copy.deepcopy(state["tcp_points"][0])
    point["id"] = "duplicate-test"
    state["tcp_points"].append(point)
    assert any("already used" in e for e in validate_configuration(state)["errors"])


def test_physical_configuration_rows_cannot_be_deleted():
    state=copy.deepcopy(load_pack())
    channel=state["io_channels"][0]
    with pytest.raises(ValueError,match="Deleting io_channels rows is not supported"):
        apply_changes(state,[{"collection":"io_channels","id":channel["id"],"op":"delete"}])


def test_client_ranges_parse_edit_and_hmi_offset():
    state = copy.deepcopy(load_pack())
    assert client_ranges(state, "FUXA")["discrete_inputs"] == [10001, 11000]
    assert client_ranges(state, "HMI")["discrete_inputs"] == [11001, 12000]
    set_client_ranges(state, "FUXA", {"coils":[10,200], "discrete_inputs":[10010,10900], "input_registers":[25,900], "holding_registers":[30,500]})
    assert state["tcp_clients"][0]["Coils range"] == "10-200"
    assert client_ranges(state, "HMI")["coils"] == [1010,1200]
    with pytest.raises(ValueError, match="Unknown TCP client"):
        client_ranges(state, "missing")


def test_creating_tcp_client_requires_and_persists_all_ranges():
    state = copy.deepcopy(load_pack())
    ranges={"coils":[2001,2100],"discrete_inputs":[12001,12100],"input_registers":[2001,2100],"holding_registers":[2001,2100]}
    apply_changes(state,[{"collection":"tcp_clients","op":"create","id":"TEST_SCADA","values":{"Client":"TEST_SCADA","Role":"PLC = server","IP address":"10.0.0.8","ranges":ranges}}])
    created=next(c for c in state["tcp_clients"] if c["id"]=="TEST_SCADA")
    assert client_ranges(state,"TEST_SCADA")==ranges
    assert created["ip_address"]=="10.0.0.8"
    with pytest.raises(ValueError,match="requires ranges"):
        apply_changes(copy.deepcopy(load_pack()),[{"collection":"tcp_clients","op":"create","id":"BAD","values":{"Client":"BAD"}}])


def test_allocator_respects_ranges_global_occupancy_pairs_and_discrete_client_address():
    state = copy.deepcopy(load_pack())
    state["instances"].append({"id":"NEW_TEST","name":"NEW_TEST","type_id":"DIP1500"})
    added = allocate_tcp_points(state, "NEW_TEST", "FUXA")
    assert added
    fuxa = [r for r in added if r["Client"] == "FUXA"]
    hmi = [r for r in added if r["Client"] == "HMI"]
    assert fuxa and hmi
    for row in fuxa:
        if row["table"] == "discrete_inputs": assert row["Client address"] == row["Modbus address"] + 90000
    for row in hmi:
        match = next((p for p in fuxa if p["table"] == row["table"] and p["Name"] == row["Name"] and p.get("Word")==row.get("Word")), None)
        if match: assert row["Modbus address"] == match["Modbus address"] + 1000
        if row["table"] == "discrete_inputs": assert row["Client address"] == row["Modbus address"]
    lsw = next(r for r in fuxa if r.get("Word") == "LSW")
    msw = next(r for r in fuxa if r.get("Word") == "MSW" and r.get("Name") == lsw.get("Name"))
    assert abs(lsw["Modbus address"] - msw["Modbus address"]) == 1
    assert allocate_tcp_points(state, "NEW_TEST", "HMI") == []
    assert len(free_addresses(state, "FUXA", "coils", 1)) == 1

def test_io_unassign_preserves_physical_mapping_and_rejects_wrong_member_type():
    state=copy.deepcopy(load_pack())
    row=next(r for r in state['io_channels'] if r.get('Instance')=='DOSENSOR_1')
    original=row['GVL_IOMAP variable']
    apply_changes(state,[{'collection':'io_channels','id':row['id'],'op':'update','values':{'Instance':''}}])
    assert row['GVL_IOMAP variable']==original
    assert not row['GVL_IO member']
    assert not row['member_name']
    spare=next(r for r in state['io_channels'] if r.get('I/O type')=='AI' and not r.get('GVL_IOMAP variable'))
    with pytest.raises(ValueError,match='type must match'):
        apply_changes(state,[{'collection':'io_channels','id':spare['id'],'op':'update','values':{'Instance':'DOSENSOR_1','GVL_IO member':'INPUT_SIGNAL','GVL_IOMAP variable':'GVL_IOMAP.NEW_SIGNAL','IOMAP type':'INT'}}])
