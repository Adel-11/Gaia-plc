from __future__ import annotations

import copy

import copy
from pathlib import Path

from lxml import etree

from backend.v2_codesys import PLC, _fingerprint, preview_codesys
from backend.v2_engine import load_pack
from backend.v2_plc import build_program, serialize_program

ROOT = Path(__file__).resolve().parents[1]


def _upload(state, names=None):
    objects = build_program(state)
    return serialize_program(objects, state, names or list(objects))


def test_same_snapshot_refresh_and_partial_merge_retains_objects():
    state = copy.deepcopy(load_pack())
    baseline = preview_codesys(_upload(state), "first.xml", state)
    assert baseline["errors"] == []
    old_count = len(baseline["state"]["snapshot"]["objects"])
    assert old_count > 20
    same = preview_codesys(_upload(baseline["state"]), "again.xml", baseline["state"])
    assert same["errors"] == []
    assert all(row["status"] == "unchanged" for row in same["changes"])
    partial = preview_codesys(_upload(state, ["GVL_COM"]), "partial.xml", baseline["state"])
    assert len(partial["state"]["snapshot"]["objects"]) == old_count


def test_import_classifies_codesys_change_and_preserves_model_and_conflict():
    state = copy.deepcopy(load_pack())
    raw = _upload(state)
    base = preview_codesys(raw, "base.xml", state)["state"]
    root = etree.fromstring(raw)
    pou = root.xpath(".//*[local-name()='pou' and @name='FB_UPI_LOCAL_FUXA']")[0]
    xhtml = pou.xpath(".//*[local-name()='ST']/*[local-name()='xhtml']")[0]
    xhtml.text = (xhtml.text or "") + "\nTRUE;"
    edited = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    result = preview_codesys(edited, "edited.xml", base)
    row = next(x for x in result["changes"] if x["name"] == "FB_UPI_LOCAL_FUXA")
    assert row["status"] == "codesys_only"
    assert row["conflict"] is True
    assert row["can_adopt"] is True
    assert "FB_UPI_LOCAL_FUXA" in result["state"]["codesys_conflicts"]
    assert result["state"]["types"] == base["types"]


def test_malicious_xml_duplicate_names_and_oversize_are_rejected():
    state = copy.deepcopy(load_pack())
    malicious = b'<!DOCTYPE project [<!ENTITY x SYSTEM "file:///C:/Windows/win.ini">]><project xmlns="' + PLC.encode() + b'"><types><dataTypes/><pous/></types><contentHeader name="&x;"/></project>'
    assert preview_codesys(malicious, "bad.xml", state)["errors"]
    duplicate = b'<project xmlns="' + PLC.encode() + b'"><types><dataTypes><dataType name="DUT_X"/><dataType name="DUT_X"/></dataTypes><pous/></types></project>'
    assert preview_codesys(duplicate, "dup.xml", state)["errors"]
    assert preview_codesys(b" " * (20 * 1024 * 1024 + 1), "large.xml", state)["errors"]


def test_snapshot_only_actual_objects_and_full_does_not_erase_engineering_data():
    state = copy.deepcopy(load_pack())
    state["private_note"] = "keep"
    raw = _upload(state)
    base = preview_codesys(raw, "base.xml", state)["state"]
    partial = preview_codesys(_upload(state, ["GVL_COM"]), "one.xml", base, "partial")["state"]
    parsed = etree.fromstring(partial["snapshot"]["xml"].encode())
    assert not any(etree.QName(x).localname in {"resource", "Application", "device", "task"} for x in parsed.iter())
    full = preview_codesys(_upload(state, ["GVL_COM"]), "full.xml", base, "full")
    assert len(full["state"]["snapshot"]["objects"]) == 1
    assert sum(row["status"] == "absent" for row in full["changes"]) == len(base["snapshot"]["objects"]) - 1
    assert full["state"]["private_note"] == "keep"
    assert full["warnings"]


def test_wrapped_latest_folders_and_string_literals_are_preserved_semantically():
    state = copy.deepcopy(load_pack())
    raw = (ROOT / "reference-v2" / "latest_codesys.xml").read_bytes()
    result = preview_codesys(raw, "latest_codesys.xml", state)
    assert result["errors"] == []
    folders = {o["name"]: o["folder"] for o in result["state"]["snapshot"]["objects"]}
    assert folders["GVL_COM"] == "GVL"
    assert folders["FB_INST_DIP1500"] == "Function Blocs/FB/03-FB_KAMOER_DIP1500"
    canonical = etree.fromstring(result["state"]["snapshot"]["xml"].encode())
    assert canonical.find("p:instances/p:configurations", {"p": PLC}) is not None
    assert canonical.find("p:contentHeader/p:coordinateInfo", {"p": PLC}) is not None
    update = etree.fromstring(_upload(copy.deepcopy(state), ["GVL_COM"]))
    update.remove(update.find("p:fileHeader", {"p": PLC}))
    update.remove(update.find("p:contentHeader", {"p": PLC}))
    refreshed = preview_codesys(etree.tostring(update), "headerless.xml", result["state"])
    refreshed_root = etree.fromstring(refreshed["state"]["snapshot"]["xml"].encode())
    assert refreshed_root.find("p:contentHeader/p:coordinateInfo", {"p": PLC}) is not None
    root = etree.fromstring(_upload(copy.deepcopy(load_pack())))
    pou = root.xpath(".//*[local-name()='pou' and @name='FB_UPI_LOCAL_FUXA']")[0]
    html = pou.xpath(".//*[local-name()='ST']/*[local-name()='xhtml']")[0]
    html.text = "message := 'two  spaces';"
    left = _fingerprint(pou)
    html.text = "message := 'two spaces';"
    assert _fingerprint(pou) != left


def test_partial_import_rejects_objectid_collision_with_retained_object():
    state = copy.deepcopy(load_pack())
    base = preview_codesys(_upload(state), "base.xml", state)["state"]
    raw = _upload(state, ["GVL_COM"])
    root = etree.fromstring(raw)
    duplicate_id = next(v for n, v in base["snapshot"]["object_ids"].items() if n != "GVL_COM")
    for node in root.iter():
        if etree.QName(node).localname == "Object" and node.get("Name") == "GVL_COM":
            node.set("ObjectId", duplicate_id)
        if etree.QName(node).localname == "ObjectId":
            node.text = duplicate_id
    result = preview_codesys(etree.tostring(root), "collision.xml", base)
    assert any("shared" in error for error in result["errors"])
