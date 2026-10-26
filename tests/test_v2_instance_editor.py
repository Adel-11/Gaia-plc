from __future__ import annotations

from pathlib import Path

import pytest

from backend.v2_engine import load_pack
from backend.v2_instance_editor import _add_instance, _configuration, _sync_workbooks, _tcp_variable, _tcp_range


def test_instance_create_only_appends_declaration_and_reads_pa_defaults_lazily():
    import copy
    state = copy.deepcopy(load_pack())
    typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    before = {key: copy.deepcopy(state.get(key, [])) for key in (
        "pa_values", "tcp_points", "rtu_slaves", "rtu_channels", "io_channels", "exchange_calls", "exchange_functions"
    )}
    pa_sheets = {sheet["name"] for sheet in state["workbooks"]["PARAMETERS_PA.xlsx"]["sheets"]}
    _add_instance(state, "DIP1500_2", typ, {})
    assert state["instances"][-1] == {"id": "DIP1500_2", "name": "DIP1500_2", "type_id": typ["id"]}
    assert {key: state.get(key, []) for key in before} == before
    assert {sheet["name"] for sheet in state["workbooks"]["PARAMETERS_PA.xlsx"]["sheets"]} == pa_sheets
    assert all("DIP1500_2" not in members for members in state["program_membership"].values())
    config = _configuration(state, state["instances"][-1])
    expected_members = [m["name"] for m in next(d for d in typ["duts"] if d["role"] == "PA")["members"]]
    assert [row["Member"] for row in config["pa_values"]] == expected_members
    assert state["pa_values"] == before["pa_values"]


def test_instance_editor_rejects_name_collision():
    import copy
    state = copy.deepcopy(load_pack())
    typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    with pytest.raises(ValueError, match="already exists"):
        _add_instance(state, "DIP1500_1", typ, {})


def test_configuration_keeps_to_check_slave_marker():
    state = load_pack()
    inst = next(i for i in state["instances"] if i["name"] == "ADM100W_1")
    data = _configuration(state, inst)
    assert any(r.get("Slave address") == "TO CHECK" for r in data["rtu_slaves"])


def test_copy_with_communication_templates_does_not_materialize_instance_rows():
    import copy
    state = copy.deepcopy(load_pack())
    typ = copy.deepcopy(next(t for t in state["types"] if t["name"] == "DIP1500"))
    typ.update(id="DIP_COPY", name="DIP_COPY")
    state["types"].append(typ)
    before = {key: len(state.get(key, [])) for key in (
        "pa_values", "tcp_points", "rtu_slaves", "rtu_channels", "io_channels", "exchange_calls", "exchange_functions"
    )}
    warnings = []
    _add_instance(state, "DIP_COPY_1", typ, {"COMMUNICATION_ADDRESS": 0}, warnings)
    assert {key: len(state.get(key, [])) for key in before} == before
    assert not any(c.get("instance_b_id") == "DIP_COPY_1" or c.get("instance_a_id") == "DIP_COPY_1" for c in state["exchange_calls"])
    assert warnings == []


def test_instance_preview_apply_stale_token_and_delete_releases_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "instance-editor")
    monkeypatch.setenv("ADMIN_PASSWORD", "instance-editor-password")
    app = create_app(data_dir=tmp_path, secret_key="instance-editor-test-" * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post("/api/login", json={"username":"instance-editor","password":"instance-editor-password"})
        headers={"X-CSRF-Token":login.json()["csrf_token"]}
        project=client.get("/api/v2/project").json()
        adm=client.get("/api/v2/instances/ADM100W_1/configuration").json()
        to_check=next(s for s in adm["rtu_slaves"] if s.get("Slave address")=="TO CHECK")
        unchanged=client.post("/api/v2/instances/preview",headers=headers,json={"revision":project["revision"],"action":"modbus","instance_id":"ADM100W_1","rtu_slaves":[{"id":to_check["id"],"address":"TO CHECK"}]})
        assert unchanged.status_code==200 and not unchanged.json()["errors"]
        payload={"revision":project["revision"],"action":"create","name":"DIP1500_2","type_id":"DIP1500"}
        assert client.post("/api/v2/instances/preview",json=payload).status_code==403
        preview=client.post("/api/v2/instances/preview",headers=headers,json=payload)
        assert preview.status_code==200 and preview.json()["errors"]==[]
        proposal=preview.json()["configuration"]
        assert proposal["pa_values"] and proposal["tcp_points"] == [] and proposal["rtu_channels"] == []
        from backend.db import ImportPreview
        import json
        with app.state.session_factory() as session:
            staged=json.loads(session.get(ImportPreview,preview.json()["token"]).state_payload)
        assert not any("DIP1500_2" in names for names in staged["program_membership"].values())
        token=preview.json()["token"]
        applied=client.post("/api/v2/instances/apply",headers=headers,json={"token":token})
        assert applied.status_code==200
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":token}).status_code==404
        after=client.get("/api/v2/project").json()
        created_config=client.get("/api/v2/instances/DIP1500_2/configuration").json()
        assert created_config["tcp_points"] == [] and created_config["rtu_channels"] == []

        # Address, PA, and physical I/O editors still work with project fixture rows.
        config=client.get("/api/v2/instances/DIP1500_1/configuration").json()
        first=config["tcp_points"][0]
        occupied=next(p for p in after["tcp_points"] if p.get("table")==first.get("table") and p.get("Client")==first.get("Client") and p.get("Instance")!="DIP1500_1")
        collision=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"modbus","instance_id":"DIP1500_1","tcp_points":[{"id":first["id"],"address":occupied["address"]}]})
        assert any("already in use" in e for e in collision.json()["errors"])
        used={int(p["address"]) for p in after["tcp_points"] if p.get("table")==first.get("table") and p.get("Client")==first.get("Client") and str(p.get("address","")).isdigit() and p["id"]!=first["id"]}
        lo,hi=_tcp_range(first.get("Client"),first.get("table"))
        moved_address=next(n for n in range(hi,lo-1,-1) if n not in used)
        moved=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"modbus","instance_id":"DIP1500_1","tcp_points":[{"id":first["id"],"address":moved_address}]})
        assert moved.status_code==200 and not moved.json()["errors"]
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":moved.json()["token"]}).status_code==200
        after=client.get("/api/v2/project").json()
        moved_row=next(p for p in after["tcp_points"] if p["id"]==first["id"])
        assert moved_row["address"]==moved_address and moved_row["GVL_MODBUS variable"]==_tcp_variable(moved_row["table"],moved_address)
        invalid_pa=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"pa","instance_id":"DIP1500_1","values":{"COMMUNICATION_ADDRESS":"70000"}})
        assert any("valid WORD" in e for e in invalid_pa.json()["errors"])
        saved_pa=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"pa","instance_id":"DIP1500_1","values":{"COMMUNICATION_ADDRESS":"17"}})
        assert saved_pa.status_code==200 and not saved_pa.json()["errors"]
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":saved_pa.json()["token"]}).status_code==200
        after=client.get("/api/v2/project").json()
        channels=client.get("/api/v2/instances/DIP1500_2/configuration").json()["options"]["io_channels"]
        available=next(c for c in channels if not c.get("Instance") and not c.get("instance_id") and str(c.get("I/O type","")).startswith("D"))
        io_preview=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"io","instance_id":"DIP1500_2","io_channels":[{"id":available["id"],"member":"SPARE"}]})
        assert io_preview.status_code==200 and not io_preview.json()["errors"]
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":io_preview.json()["token"]}).status_code==200
        assert any(c.get("id")==available["id"] and c.get("instance_id")=="DIP1500_2" for c in client.get("/api/v2/project").json()["io_channels"])
        current=client.get("/api/v2/project").json()
        stale=client.post("/api/v2/instances/preview",headers=headers,json={"revision":project["revision"],"action":"delete","instance_id":"DIP1500_2"})
        assert stale.status_code==409
        delete=client.post("/api/v2/instances/preview",headers=headers,json={"revision":current["revision"],"action":"delete","instance_id":"DIP1500_2"})
        assert delete.status_code==200 and any("release" in c for c in delete.json()["changes"])
        saved=client.post("/api/v2/instances/apply",headers=headers,json={"token":delete.json()["token"]})
        assert saved.status_code==200
        final=client.get("/api/v2/project").json()
        assert not any(i["name"]=="DIP1500_2" for i in final["instances"])
        assert not any(p.get("Instance")=="DIP1500_2" for p in final["tcp_points"])
        released=next(c for c in final["io_channels"] if c["id"]==available["id"])
        assert released.get("Instance") is None and released.get("Status")=="Spare"
