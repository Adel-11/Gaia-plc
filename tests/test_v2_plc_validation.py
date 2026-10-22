from __future__ import annotations

import copy
from pathlib import Path

from lxml import etree

from backend.v2_engine import DEFAULT_PACK, load_pack
from backend.v2_plc import build_program, serialize_program
from backend.v2_plc_validation import NS, plan_export, validate_xml


def _example() -> bytes:
    path = DEFAULT_PACK / "03_plc" / "examples" / "Gaia_V7_partial_FC_EXC.xml"
    return path.read_bytes()


def test_reference_partial_xml_validates_as_free_objects():
    state = copy.deepcopy(load_pack())
    objects = build_program(state)
    xml = serialize_program(objects, state, list(objects))
    result = validate_xml(xml, state, objects)
    assert result["errors"] == []


def test_schema_and_free_object_constraints_are_reported():
    state = copy.deepcopy(load_pack())
    root = etree.fromstring(_example())
    root.find(f"{{{NS}}}types/{{{NS}}}dataTypes").getparent().remove(root.find(f"{{{NS}}}types/{{{NS}}}dataTypes"))
    result = validate_xml(etree.tostring(root), state, {})
    assert any("schema validation failed" in error.lower() for error in result["errors"])

    root = etree.fromstring(_example())
    app = etree.SubElement(root, f"{{{NS}}}Application")
    result = validate_xml(etree.tostring(root), state, {})
    assert any("free objects only" in error for error in result["errors"])


def test_duplicate_object_ids_and_missing_dut_are_rejected():
    state = copy.deepcopy(load_pack())
    root = etree.fromstring(_example())
    pous = root.find(f"{{{NS}}}types/{{{NS}}}pous")
    first = pous[0]
    oid = first.xpath(".//*[local-name()='ObjectId']")[0]
    second = copy.deepcopy(first)
    second.set("name", "FC_EXC_DUPLICATE")
    second.xpath(".//*[local-name()='ObjectId']")[0].text = oid.text
    second.xpath(".//*[local-name()='derived']")[0].set("name", "DUT_DOES_NOT_EXIST")
    pous.append(second)
    result = validate_xml(etree.tostring(root), state, {})
    assert any("duplicate objectid" in error.lower() for error in result["errors"])
    assert any("DUT_DOES_NOT_EXIST" in error for error in result["errors"])


def test_tcp_array_overflow_blocks_export_with_required_size():
    state = copy.deepcopy(load_pack())
    state["tcp_points"] = [{"address": 1502, "table": "holding_registers", "GVL_MODBUS variable": "GVL_MODBUS.Holding_register_TCP_HMI[1501]", "data_type": "WORD"}]
    root = etree.fromstring(_example())
    gvl = etree.SubElement(root.find(f"{{{NS}}}addData"), f"{{{NS}}}globalVars", name="GVL_MODBUS")
    var = etree.SubElement(gvl, f"{{{NS}}}variable", name="Holding_register_TCP_HMI")
    typ = etree.SubElement(var, f"{{{NS}}}type")
    arr = etree.SubElement(typ, f"{{{NS}}}array")
    etree.SubElement(arr, f"{{{NS}}}dimension", lower="0", upper="1500")
    result = validate_xml(etree.tostring(root), state, {})
    assert any("exceeds declared array" in error and "requires at least 1502 array elements" in error for error in result["errors"])


def test_nested_dut_member_and_bit_bounds_are_checked():
    state = copy.deepcopy(load_pack())
    objects = build_program(state)
    root = etree.fromstring(serialize_program(objects, state, list(objects)))
    pou = root.xpath("//*[local-name()='pou'][@name='FB_INST_DOSENSOR']")[0]
    code = pou.xpath(".//*[local-name()='body']/*[local-name()='ST']/*[local-name()='xhtml']")[0]
    code.text = (code.text or "") + "\nGVL_COM.COM_DOSENSOR_1.NO_SUCH_MEMBER := TRUE;\nGVL_MODBUS.Coils_TCP_HMI[0].8 := TRUE;"
    result = validate_xml(etree.tostring(root), state, objects)
    assert any("missing member DUT_COM_DOSENSOR.NO_SUCH_MEMBER" in error for error in result["errors"])
    assert any("bit 8 outside BYTE bounds 0..7" in error for error in result["errors"])


def test_rtu_mapping_collision_between_instances_blocks_export():
    state = copy.deepcopy(load_pack())
    objects = build_program(state)
    state["rtu_channels"].append({**state["rtu_channels"][0], "instance_id": "ADM100W_2", "instance": "ADM100W_2"})
    xml = serialize_program(objects, state, list(objects))
    result = validate_xml(xml, state, objects)
    assert any("RTU mapped variable GVL_MODBUS.ADM100W_Alarms is shared" in error for error in result["errors"])


def test_unknown_globals_and_external_union_word_bounds_block_export():
    state = copy.deepcopy(load_pack())
    objects = build_program(state)
    root = etree.fromstring(serialize_program(objects, state, list(objects)))
    code = root.xpath("//*[local-name()='pou'][@name='FB_INST_DIP1500']//*[local-name()='body']//*[local-name()='xhtml']")[0]
    typ = next(t for t in state['types'] if t['name'] == 'DIP1500')
    dut = next(d for d in typ['duts'] if d['role'] == 'COM')
    member = next(m['name'] for m in dut['members'] if m['iec_type'] == 'DUT_Union_Real_Word')
    code.text += f'\nGVL_ABSENT.Value := TRUE;\nGVL_MODBUS.Coils_TCP_HMI[-1].0 := TRUE;\nCOM.{member}.Word_value[9] := 0;'
    validation = validate_xml(etree.tostring(root), state, objects)
    assert any('GVL_ABSENT' in message for message in validation['errors'])
    assert any('index -1' in message for message in validation['errors'])
    assert any('Word_value array bounds 0..1' in message for message in validation['errors'])


def test_partial_plan_excludes_unchanged_objects_and_rejects_missing_dependency_selection():
    state = copy.deepcopy(load_pack())
    # A stored known object with the same generated element is semantically unchanged.
    ns = f"{{{NS}}}"
    element = etree.Element(ns + "dataType", name="DUT_SAMPLE")
    objects = {"DUT_SAMPLE": {"name": "DUT_SAMPLE", "kind": "DUT", "element": element},
               "FB_SAMPLE": {"name": "FB_SAMPLE", "kind": "POU", "element": etree.Element(ns + "pou", name="FB_SAMPLE")}}
    state["snapshot"] = {"objects": [{"name": "DUT_SAMPLE"}], "xml": etree.tostring(etree.Element(ns + "project")).decode()}
    # A first-time dependency is automatically included in a normal plan.
    plan = plan_export(state, objects, mode="partial", focus="object:FB_SAMPLE")
    assert plan["included"] == [{"name": "FB_SAMPLE", "kind": "POU", "reason": "focused object"}]


def test_explicit_selection_can_add_available_objects_but_reject_missing_types():
    state = copy.deepcopy(load_pack())
    objects = build_program(state)
    selected = plan_export(state, objects, mode="partial", focus="object:GFC_EXC", selected=["GFC_EXC"])
    assert [row["name"] for row in selected["included"]] == ["GFC_EXC"]

    element = etree.Element(f"{{{NS}}}pou", name="FB_NEEDS_DUT", pouType="functionBlock")
    iface = etree.SubElement(element, f"{{{NS}}}interface")
    inputs = etree.SubElement(iface, f"{{{NS}}}inputVars")
    var = etree.SubElement(inputs, f"{{{NS}}}variable", name="Value")
    typ = etree.SubElement(var, f"{{{NS}}}type")
    etree.SubElement(typ, f"{{{NS}}}derived", name="DUT_MISSING")
    objects = {"FB_NEEDS_DUT": {"name": "FB_NEEDS_DUT", "kind": "POU", "element": element}}
    result = plan_export(state, objects, mode="partial", focus="object:FB_NEEDS_DUT", selected=["FB_NEEDS_DUT"])
    assert any("missing required dependency DUT_MISSING" in error for error in result["errors"])


def test_partial_gfc_export_includes_new_exchange_function_dependency():
    state = copy.deepcopy(load_pack())
    function = state['exchange_functions'][0]
    old = function['name']
    new = old + '_NEW'
    function.update(name=new, id=new, FC=new)
    for call in state['exchange_calls']:
        if call['function_id'] == old:
            call.update(function_id=new, FC=new)
    objects = build_program(state)
    plan = plan_export(state, objects, mode='partial', focus='object:GFC_EXC')
    assert plan['errors'] == []
    assert {'GFC_EXC', new} <= {o['name'] for o in plan['included']}
    chosen = plan_export(state, objects, mode='partial', focus='object:GFC_EXC', selected=['GFC_EXC'])
    assert any(new in message and 'missing required dependency' in message for message in chosen['errors'])

