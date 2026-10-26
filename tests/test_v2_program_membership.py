from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.v2_engine import load_pack
from backend.v2_plc import HNS, NS, PLC, build_program, serialize_program
from backend.v2_plc_validation import validate_xml
from backend.v2_program_membership import BLOCKS, freeze_membership


def _body(objects, name):
    return objects[name]["element"].find(NS + "body/" + NS + "ST/" + HNS + "xhtml").text or ""


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
