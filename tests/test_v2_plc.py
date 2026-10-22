from __future__ import annotations

import copy
from lxml import etree

from backend.v2_engine import load_pack
from backend.v2_plc import PLC, build_program, serialize_program
from backend.v2_plc_validation import validate_xml


def _state():
    return copy.deepcopy(load_pack())


def test_full_export_contains_reference_free_objects_without_union_or_application():
    state = _state()
    objects = build_program(state)
    assert len(objects) == 55
    assert "DUT_Union_Real_Word" not in objects
    xml = serialize_program(objects, state, list(objects))
    root = etree.fromstring(xml)
    ns = {"p": PLC}
    assert root.find("p:types/p:dataTypes", ns) is not None
    assert root.find("p:types/p:pous", ns) is not None
    assert not any(etree.QName(node).localname in {"Application", "device", "task", "library"} for node in root.iter())
    assert len(root.xpath(".//p:ProjectStructure//p:Object", namespaces=ns)) == 55


def test_partial_export_has_only_selected_objects_and_stable_ids():
    state = _state(); objects = build_program(state)
    names = ["DUT_COM_DIP1500", "FB_INST_DIP1500", "GVL_COM"]
    root = etree.fromstring(serialize_program(objects, state, names))
    ns = {"p": PLC}
    exported = [n.get("name") for n in root.xpath(".//p:dataType|.//p:pou|.//p:globalVars", namespaces=ns)]
    assert set(exported) == set(names)
    ids = {o["name"]: o["id"] for o in state["snapshot"]["objects"]}
    for name in names:
        assert root.xpath("string(.//p:ProjectStructure//p:Object[@Name=$n]/@ObjectId)", namespaces=ns, n=name) == ids[name]


def test_structured_member_edits_flow_into_generated_dut():
    state = _state(); typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    dut = next(d for d in typ["duts"] if d["role"] == "COM")
    dut["members"][0]["name"] = "MODEL_EDITED_MEMBER"
    obj = build_program(state)[dut["name"]]["element"]
    assert obj.xpath("string(.//p:variable[1]/@name)", namespaces={"p": PLC}) == "MODEL_EDITED_MEMBER"


def test_custom_code_is_preserved_as_a_separate_verbatim_section():
    state = _state(); typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    typ["fb"]["custom_code"] = "\n// user addition\nINTERN.TEST := TRUE;\n"
    objs = build_program(state); body = objs[typ["fb"]["name"]]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    assert "// user addition\nINTERN.TEST := TRUE;" in body


def test_function_custom_code_precedes_final_return_assignment():
    state = _state()
    fn = next(f for f in state["functions"] if f["name"] == "FNC_REAL_TO_WORDS")
    fn["custom_code"] = "// custom function work\n"
    body = build_program(state)[fn["name"]]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    assert body.index("// custom function work") < body.rindex("FNC_REAL_TO_WORDS :=")


def test_tcp_address_change_recomputes_array_mapping():
    state = _state()
    point = next(p for p in state["tcp_points"] if p["Client"] == "FUXA" and p["table"] == "coils")
    point["Modbus address"] = 9
    objects = build_program(state)
    body = objects["GFC_COM_IN"]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    assert "GVL_MODBUS.Coils_TCP_HMI[1].0" in body
    assert "GVL_MODBUS.Coils_TCP_HMI[0].0" not in body


def test_pa_override_and_exchange_call_values_are_model_driven():
    state = _state()
    override = next(p for p in state["pa_values"] if p.get("instance") == "DIP1500_1")
    override["value"] = 321
    call = state["exchange_calls"][0]
    call["parameter_a_value"] = "GVL_EXC.EXC_UPI_LOCAL_FUXA"
    call["parameter_b_value"] = "GVL_EXC.EXC_ADM100W_1"
    objects = build_program(state)
    pa = objects["GVL_PA"]["element"]
    assert pa.xpath(".//p:variable[@name='PA_DIP1500_1']//p:simpleValue[@value='321']", namespaces={"p": PLC})
    gfc = objects["GFC_EXC"]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    assert "GVL_EXC.EXC_UPI_LOCAL_FUXA" in gfc and "GVL_EXC.EXC_ADM100W_1" in gfc


def test_new_instance_has_gvl_and_gfb_declarations_and_pa_override():
    state = _state()
    state["instances"].append({"id": "DIP1500_2", "name": "DIP1500_2", "type_id": "DIP1500"})
    row = copy.deepcopy(next(p for p in state["pa_values"] if p.get("instance") == "DIP1500_1"))
    row.update({"instance": "DIP1500_2", "instance_id": "DIP1500_2", "value": 222})
    state["pa_values"].append(row)
    objects = build_program(state)
    assert objects["GVL_COM"]["element"].xpath("./p:variable[@name='COM_DIP1500_2']", namespaces={"p": PLC})
    assert objects["GVL_PA"]["element"].xpath("./p:variable[@name='PA_DIP1500_2']//p:simpleValue[@value='222']", namespaces={"p": PLC})
    gfb = objects["GFB_INST"]["element"]
    assert gfb.xpath("./p:interface/p:localVars/p:variable[@name='FB_DIP1500_2']", namespaces={"p": PLC})
    assert "FB_DIP1500_2(" in gfb.xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    xml = serialize_program(objects, state, list(objects))
    assert validate_xml(xml, state, objects)["errors"] == []


def test_removed_type_instance_and_exchange_function_are_not_generated():
    state = _state()
    state["types"] = [t for t in state["types"] if t["name"] != "DOSENSOR"]
    state["instances"] = [i for i in state["instances"] if i["name"] != "DOSENSOR_1"]
    state["exchange_functions"] = [f for f in state["exchange_functions"] if "DOSENSOR" not in str(f.get("name"))]
    objects = build_program(state)
    assert "DUT_COM_DOSENSOR" not in objects and "FB_INST_DOSENSOR" not in objects
    assert not any(n.startswith("FC_EXC_") and "DOSENSOR" in n for n in objects)
    for name in ("GFB_INST", "GFC_COM_IN", "GFC_COM_OUT", "GFC_IO_IN", "GFC_IO_OUT", "GFC_EXC"):
        body = objects[name]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
        assert "DOSENSOR_1" not in body


def test_current_fb_body_and_snapshot_headers_are_used():
    state = _state()
    typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    typ["fb"]["body"] = "INTERN.MODE := TRUE;\n"
    state["snapshot"]["file_header"]["productVersion"] = "USER-SNAPSHOT"
    state["snapshot"]["content_header"]["name"] = "Current.project"
    objects = build_program(state)
    fb_body = objects[typ["fb"]["name"]]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})
    assert fb_body == "INTERN.MODE := TRUE;\n"
    xml = etree.fromstring(serialize_program(objects, state, [typ["fb"]["name"]]))
    ns = {"p": PLC}
    assert xml.find("p:fileHeader", ns).get("productVersion") == "USER-SNAPSHOT"
    assert xml.find("p:contentHeader", ns).get("name") == "Current.project"


def test_new_persistent_list_keeps_qualified_only_and_drops_internal_checksums():
    state = _state()
    state["instances"].append({"id": "DIP1500_2", "name": "DIP1500_2", "type_id": "DIP1500"})
    pa = build_program(state)["GVL_PA"]["element"]
    attrs = pa.xpath("./p:addData/p:data[@name='http://www.3s-software.com/plcopenxml/attributes']/p:Attributes/p:Attribute/@Name", namespaces={"p": PLC})
    assert "qualified_only" in attrs
    assert "checksumnoinit_override" not in attrs and "init_related_code" not in attrs


def test_new_explicit_modbus_and_iomap_names_get_inferred_declarations():
    state = _state()
    state["instances"].append({"id": "DIP1500_2", "name": "DIP1500_2", "type_id": "DIP1500"})
    rtu = copy.deepcopy(next(p for p in state["rtu_channels"] if p.get("instance") == "DIP1500_1" and p.get("Direction") == "PLC to Equipment"))
    rtu.update({"instance": "DIP1500_2", "instance_id": "DIP1500_2", "GVL_MODBUS variable": "GVL_MODBUS.DIP1500_2_Coils", "GVL_COM variable": "GVL_COM.COM_DIP1500_2.COM_OUTPUT_COILS"})
    state["rtu_channels"].append(rtu)
    io = copy.deepcopy(state["io_channels"][0])
    io.update({"Instance": "DIP1500_2", "instance_id": "DIP1500_2", "GVL_IOMAP variable": "GVL_IOMAP.IOMAP_NEW_ANALOG", "IOMAP type": "DWORD", "GVL_IO member": "GVL_IO.IO_DIP1500_2.INPUT_SIGNAL"})
    state["io_channels"].append(io)
    objects = build_program(state)
    modbus = objects["GVL_MODBUS"]["element"]
    iomap = objects["GVL_IOMAP"]["element"]
    assert modbus.xpath("./p:variable[@name='DIP1500_2_Coils']/p:type/p:array/p:baseType/p:BOOL", namespaces={"p": PLC})
    assert iomap.xpath("./p:variable[@name='IOMAP_NEW_ANALOG']/p:type/p:DWORD", namespaces={"p": PLC})
    assert "GVL_MODBUS.DIP1500_2_Coils := GVL_COM.COM_DIP1500_2.COM_OUTPUT_COILS;" in objects["GFC_COM_OUT"]["element"].xpath("string(.//p:body/p:ST/h:xhtml)", namespaces={"p": PLC, "h": "http://www.w3.org/1999/xhtml"})

