from __future__ import annotations

import copy
import json

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.v2_engine import load_pack
from backend.v2_instance_editor import _add_instance, _configuration, _remove_program_membership
from backend.v2_type_creation import _type_validation_closure


def test_new_type_validation_includes_only_recursive_derived_member_owners():
    leaf = {"name": "TYPE_LEAF", "duts": [{"name": "DUT_LEAF", "members": []}]}
    middle = {"name": "TYPE_MIDDLE", "duts": [{"name": "DUT_MIDDLE", "members": [
        {"name": "leaf", "iec_type": "DUT_LEAF"}
    ]}]}
    unrelated = {"name": "TYPE_UNRELATED", "duts": [{"name": "DUT_UNRELATED", "members": [
        {"name": "bad", "iec_type": "MISSING_TYPE"}
    ]}]}
    root_type = {"name": "TYPE_NEW", "duts": [{"name": "DUT_NEW", "members": [
        {"name": "derived", "iec_type": "DUT_MIDDLE"}
    ]}]}

    selected, referenced = _type_validation_closure(root_type, [leaf, middle, unrelated])

    assert selected == [root_type, middle, leaf]
    assert referenced == {"DUT_MIDDLE", "DUT_LEAF"}


def test_instance_creation_only_adds_declaration_and_lazy_pa_defaults():
    state = copy.deepcopy(load_pack())
    typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    state["pa_values"] = []
    prior_counts = {key: len(state.get(key, [])) for key in (
        "tcp_points", "rtu_slaves", "rtu_channels", "io_channels", "exchange_calls", "exchange_functions"
    )}

    _add_instance(state, "DIP1500_SIMPLE_1", typ, {})

    assert state["instances"][-1] == {"id": "DIP1500_SIMPLE_1", "name": "DIP1500_SIMPLE_1", "type_id": typ["id"]}
    assert {key: len(state.get(key, [])) for key in prior_counts} == prior_counts
    assert state["pa_values"] == []
    assert state["program_membership"]
    assert all("DIP1500_SIMPLE_1" not in names for names in state["program_membership"].values())
    configuration = _configuration(state, state["instances"][-1])
    expected = next(d for d in typ["duts"] if d["role"] == "PA")["members"]
    assert [row["Member"] for row in configuration["pa_values"]] == [m["name"] for m in expected]
    assert state["pa_values"] == []

    for names in state["program_membership"].values():
        names.append("DIP1500_SIMPLE_1")
    _remove_program_membership(state, "DIP1500_SIMPLE_1")
    assert all("DIP1500_SIMPLE_1" not in names for names in state["program_membership"].values())


def test_type_creation_ignores_unrelated_project_errors_and_drops_communication_recipes(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "simple-create")
    monkeypatch.setenv("ADMIN_PASSWORD", "simple-create-password")
    app = create_app(data_dir=tmp_path, secret_key="simple-create-tests-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post("/api/login", json={"username": "simple-create", "password": "simple-create-password"}).json()
        headers = {"X-CSRF-Token": auth["csrf_token"]}
        from backend import v2_store as store
        with app.state.session_factory() as session:
            state = store.load_project(session)
            # Keep an unrelated invalid declaration in the existing project.
            state["types"][0]["duts"][0]["members"][0]["iec_type"] = "UNRELATED_UNKNOWN_TYPE"
            state = store.save_project(session, state, username="simple-create", expected_revision=state.get("revision", 0))
            session.commit()
        project = client.get("/api/v2/project").json()
        payload = {
            "revision": project["revision"], "name": "SIMPLE_COPY", "kind": "Equipment",
            "copy_from": "DIP1500", "communication": "TCP + RTU",
            "supervision": [{"member_name": "COM.SCADA_INPUT_LOCAL_REQUEST"}],
            "upi_bindings": [{"upi_type_id": "UPI_LOCAL_HMI", "mappings": [
                {"source": "equipment.COMMAND", "destination": "upi.COMMAND"}
            ]}],
            "rtu": {"RTU_COILS": [{"Name": "AUTOMATIC"}]},
            "rtu_default": {"Slave address": 17},
        }
        preview = client.post("/api/v2/types/preview", headers=headers, json=payload)
        assert preview.status_code == 200, preview.text
        result = preview.json()
        assert result["token"] and result["errors"] == [], result["errors"]
        with app.state.session_factory() as session:
            from backend.db import ImportPreview
            draft = session.get(ImportPreview, result["token"])
            staged = json.loads(draft.state_payload)
        created = next(t for t in staged["types"] if t["name"] == "SIMPLE_COPY")
        assert created.get("templates") == {}
        assert not created.get("upi_bindings")
        assert not created.get("supervision")
        assert all(not rows for rows in created.get("rtu", {}).values())
        assert created.get("rtu_default") == {}
        assert len(staged["instances"]) == len(project["instances"])
        assert staged.get("exchange_calls", []) == project.get("exchange_calls", [])
        assert staged.get("exchange_functions", []) == project.get("exchange_functions", [])
        assert "TYPE_SIMPLE_COPY.xlsx" in staged["workbooks"]
        assert client.post("/api/v2/types/apply", headers=headers, json={"token": result["token"]}).status_code == 200


def test_type_creation_reports_unknown_local_member_type(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "simple-create")
    monkeypatch.setenv("ADMIN_PASSWORD", "simple-create-password")
    app = create_app(data_dir=tmp_path, secret_key="simple-create-errors-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post("/api/login", json={"username": "simple-create", "password": "simple-create-password"}).json()
        headers = {"X-CSRF-Token": auth["csrf_token"]}
        project = client.get("/api/v2/project").json()
        payload = {"revision": project["revision"], "name": "BAD_LOCAL_TYPE", "kind": "Equipment",
                   "duts": [{"role": role, "members": [{"name": "VALUE", "iec_type": "MADE_UP_TYPE"}]}
                            for role in ("PA", "COM", "EXC", "IO", "INT")]}
        response = client.post("/api/v2/types/preview", headers=headers, json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result["token"] is None
        assert any("Unknown IEC type" in error and "MADE_UP_TYPE" in error for error in result["errors"])


def test_instance_creation_review_has_no_collection_side_effects_and_pa_is_lazy(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "simple-instance")
    monkeypatch.setenv("ADMIN_PASSWORD", "simple-instance-password")
    app = create_app(data_dir=tmp_path, secret_key="simple-instance-tests-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        auth = client.post("/api/login", json={"username": "simple-instance", "password": "simple-instance-password"}).json()
        headers = {"X-CSRF-Token": auth["csrf_token"]}
        project = client.get("/api/v2/project").json()
        prior_counts = {key: len(project.get(key, [])) for key in (
            "pa_values", "tcp_points", "rtu_slaves", "rtu_channels", "io_channels", "exchange_calls"
        )}
        preview = client.post("/api/v2/instances/preview", headers=headers, json={
            "revision": project["revision"], "action": "create", "name": "SIMPLE_INSTANCE_1", "type_id": "DIP1500",
            # Submitted old-style configuration recipes are ignored during creation.
            "tcp_points": [{"id": "ignored", "address": 1}], "rtu_slaves": [{"id": "ignored", "address": 1}],
        })
        assert preview.status_code == 200, preview.text
        result = preview.json()
        assert result["token"] and not result["errors"], result["errors"]
        from backend.db import ImportPreview
        with app.state.session_factory() as session:
            staged = json.loads(session.get(ImportPreview, result["token"]).state_payload)
        assert {key: len(staged.get(key, [])) for key in prior_counts} == prior_counts
        assert all("SIMPLE_INSTANCE_1" not in names for names in staged["program_membership"].values())
        assert client.post("/api/v2/instances/apply", headers=headers, json={"token": result["token"]}).status_code == 200
        configuration = client.get("/api/v2/instances/SIMPLE_INSTANCE_1/configuration").json()
        assert configuration["pa_values"]
        assert configuration["tcp_points"] == [] and configuration["rtu_channels"] == []
        member = configuration["pa_values"][0]
        saved_pa = client.post("/api/v2/instances/preview", headers=headers, json={
            "revision": configuration["revision"], "action": "pa", "instance_id": "SIMPLE_INSTANCE_1",
            "values": {member["Member"]: member.get("Default value")},
        })
        assert saved_pa.status_code == 200 and saved_pa.json()["token"], saved_pa.json()
        assert client.post("/api/v2/instances/apply", headers=headers,
                           json={"token": saved_pa.json()["token"]}).status_code == 200
        final = client.get("/api/v2/project").json()
        assert len(final["pa_values"]) == prior_counts["pa_values"] + 1
        assert any(row.get("instance_id", row.get("instance")) == "SIMPLE_INSTANCE_1" for row in final["pa_values"])
