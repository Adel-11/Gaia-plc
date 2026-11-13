from __future__ import annotations

import copy
import pytest

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.v2_engine import load_pack
from backend.v2_plc import HNS, NS, PLC, build_program, serialize_program
from backend.v2_plc_validation import validate_xml
from backend.v2_program_membership import BLOCKS, freeze_membership


def _body(objects, name):
    return objects[name]["element"].find(NS + "body/" + NS + "ST/" + HNS + "xhtml").text or ""


def test_dip1500_scalar_rtu_and_review_choices_are_scoped():
    from backend.v2_dip1500 import regularize
    from backend.v2_gfc_mappings import review, save_choices
    from backend.v2_hmi import load_hmi_pack

    state = copy.deepcopy(load_hmi_pack())
    original = copy.deepcopy(state)
    regularize(state)
    assert not regularize(state)
    assert all(state[key] == value for key, value in original.items() if key != "types")
    assert all(new == old for new, old in zip(state["types"], original["types"]) if new["name"] != "DIP1500")
    block = "GFC_COM_OUT"
    instance = "DIP1500_1"
    initial = _body(build_program(state), block)
    assert f"// {instance}" in initial
    assert "DIP1500_Holding_Cmd[0] := GVL_COM.COM_DIP1500_1.COM_OUTPUT_HR_CMD[0];" in initial
    assert "DIP1500_Holding_Cmd :=" not in initial
    group, = review(state, block, [instance])
    row = next(row for row in group["rows"] if row["endpoints"] == ["GVL_MODBUS.DIP1500_Holding_Cmd[0]"])
    assert all(path.startswith(f"GVL_COM.COM_{instance}.") for path in row["choices"])
    with pytest.raises(ValueError, match="compatible"):
        save_choices(state, block, [instance], {row["key"]: "GVL_COM.COM_ADM100W_1.COM_INPUT_IR[0]"})
    choice = next(path for path in row["choices"] if path != row["member"])
    save_choices(state, block, [instance], {row["key"]: choice})
    assert f"GVL_MODBUS.DIP1500_Holding_Cmd[0] := {choice};" in _body(build_program(state), block)
    assert state["rtu_channels"] == original["rtu_channels"]


def test_freeze_initializes_all_programs_by_equipment_and_upi_eligibility():
    state = copy.deepcopy(load_pack())
    membership = freeze_membership(state)
    equipment = {i["name"] for i in state["instances"] if "UPI" not in next(t for t in state["types"] if t["id"] == i["type_id"])["kind"].upper()}
    upis = {i["name"] for i in state["instances"] if "UPI" in next(t for t in state["types"] if t["id"] == i["type_id"])["kind"].upper()}
    assert set(membership) == set(BLOCKS)
    assert set(membership["GFB_INST"]) == equipment
    assert set(membership["GFB_UPI"]) == upis
    assert set(membership["GFC_COM_IN"]) == equipment | upis
    assert set(membership["GFC_IO_OUT"]) == equipment


def test_freezing_before_new_instance_keeps_its_call_opt_in_but_declares_it():
    state = copy.deepcopy(load_pack())
    membership = freeze_membership(state)
    state["instances"].append({"id": "ADM100W_EXTRA", "name": "ADM100W_EXTRA", "type_id": "ADM100W"})
    program = build_program(state)
    body = _body(program, "GFB_INST")
    assert "FB_ADM100W_EXTRA(" not in body
    assert program["GFB_INST"]["element"].xpath("./p:interface/p:localVars/p:variable[@name='FB_ADM100W_EXTRA']", namespaces={"p": PLC})
    assert "ADM100W_EXTRA" not in membership["GFB_INST"]


def test_empty_copy_memberships_are_return_only_and_exchange_requires_both_endpoints():
    state = copy.deepcopy(load_pack())
    membership = freeze_membership(state)
    membership["GFC_COM_IN"] = []
    membership["GFC_IO_IN"] = []
    membership["GFC_EXC"] = []
    program = build_program(state)
    assert _body(program, "GFC_COM_IN").strip() == "GFC_COM_IN := TRUE;"
    assert _body(program, "GFC_IO_IN").strip() == "GFC_IO_IN := TRUE;"
    assert _body(program, "GFC_EXC").strip() == "GFC_EXC := TRUE;"
    assert "FB_" in _body(program, "GFB_INST")  # GFB membership remains independently enabled.
    assert validate_xml(serialize_program(program, state, list(program)), state, program)["errors"] == []


def test_membership_preview_is_reviewed_owner_scoped_csrf_protected_and_revisioned(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "membership-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "membership-admin-password")
    app = create_app(data_dir=tmp_path, secret_key="membership-test-secret-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post("/api/login", json={"username": "membership-admin", "password": "membership-admin-password"})
        assert auth.status_code == 200
        headers = {"X-CSRF-Token": auth.json()["csrf_token"]}
        catalog = client.get("/api/v2/code").json()
        item = next(row for row in catalog["objects"] if row["name"] == "GFB_INST")
        assert item["included_instances"]
        assert item["missing_instances"] == []
        revision = catalog["revision"]
        included = item["included_instances"][:-1]
        body = {"revision": revision, "name": "GFB_INST", "included_instances": included}
        assert client.post("/api/v2/code/preview", json=body).status_code == 403
        assert client.post("/api/v2/code/preview", headers=headers, json={**body, "revision": revision - 1}).status_code == 409
        assert client.post("/api/v2/code/preview", headers=headers, json={**body, "included_instances": ["UNKNOWN_INSTANCE"]}).status_code == 400
        preview = client.post("/api/v2/code/preview", headers=headers, json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()["token"]
        assert preview.json()["object"]["full_body"] is not None
        assert any("instances added" in change for change in preview.json()["changes"])

        created = client.post("/api/v2/workspace/users", headers=headers, json={"username": "membership-editor", "password": "membership-editor-password", "is_admin": False})
        assert created.status_code == 200, created.text
        other = TestClient(app)
        with other:
            other_auth = other.post("/api/login", json={"username": "membership-editor", "password": "membership-editor-password"})
            other_headers = {"X-CSRF-Token": other_auth.json()["csrf_token"]}
            assert other.post("/api/v2/code/apply", headers=other_headers, json={"token": preview.json()["token"]}).status_code == 404
        applied = client.post("/api/v2/code/apply", headers=headers, json={"token": preview.json()["token"]})
        assert applied.status_code == 200, applied.text
        updated = next(row for row in client.get("/api/v2/code").json()["objects"] if row["name"] == "GFB_INST")
        assert updated["included_instances"] == included
def test_simplified_dip1500_can_map_two_instances():
    from copy import deepcopy
    from backend.v2_codesys import _st
    from backend.v2_dip1500 import simplify
    from backend.v2_hmi import load_hmi_pack
    from backend.v2_plc import build_program
    from backend.v2_workspace import _compatibility_errors
    from backend.v2_modbus_templates import apply_template, seed_rows

    state = deepcopy(load_hmi_pack())
    baseline = set(_compatibility_errors(state))
    original_rtu = [row["GVL_MODBUS variable"] for row in state["rtu_channels"]]
    assert simplify(state)
    assert not simplify(state)
    assert [row["GVL_MODBUS variable"] for row in state["rtu_channels"]] == original_rtu
    typ = next(row for row in state["types"] if row["name"] == "DIP1500")
    typ["modbus_template"] = [row.model_dump(by_alias=True) for row in seed_rows(state, typ)]
    state["instances"].append({"id": "DIP1500_2", "name": "DIP1500_2", "type_id": "DIP1500"})
    rtu, _ = apply_template(state, "DIP1500_2", "rtu", line="RTU_1", address=12, include=True)
    tcp, _ = apply_template(state, "DIP1500_2", "tcp", client="HMI", include=True)
    assert len(rtu) == 26 and len(tcp) == 30
    assert not apply_template(state, "DIP1500_2", "rtu")[0]
    assert not apply_template(state, "DIP1500_2", "tcp", client="HMI")[0]
    assert set(_compatibility_errors(state)) <= baseline
    objects = build_program(state)
    inbound = _st(objects["GFC_COM_IN"]["element"])
    assert "COM_DIP1500_1.COM_INPUT_RUN_ACTUAL.Word_value[0] := GVL_MODBUS.DIP1500_Holding_Counters[0]" in inbound
    assert "COM_DIP1500_2.COM_INPUT_RUN_ACTUAL.Word_value[0] := GVL_MODBUS.DIP1500_2_Holding_Counters[0]" in inbound
    assert not objects["FB_INST_DIP1500"]["element"].xpath(".//*[@name='R_TRIG_FLASH']")
