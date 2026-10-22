from __future__ import annotations

from pathlib import Path

import pytest

from backend.v2_engine import load_pack
from backend.v2_instance_editor import _add_instance, _configuration, _sync_workbooks, _tcp_variable, _tcp_range


def test_instance_template_clone_allocates_distinct_tcp_addresses_and_persists_workbooks():
    state = load_pack()
    state = __import__("copy").deepcopy(state)
    typ = next(t for t in state["types"] if t["name"] == "DIP1500")
    existing = {(str(p.get("Client")), str(p.get("table")), int(p["Modbus address"])) for p in state["tcp_points"] if str(p.get("Modbus address", "")).isdigit()}
    _add_instance(state, "DIP1500_2", typ, {})
    added = [p for p in state["tcp_points"] if p.get("Instance") == "DIP1500_2"]
    assert added
    assert all((str(p.get("Client")), str(p.get("table")), int(p["Modbus address"])) not in existing for p in added)
    assert all(p.get("GVL_COM variable", "").find("DIP1500_2") >= 0 for p in added)
    assert any(p.get("GVL_PA variable", "").find("DIP1500_2") >= 0 for p in state["pa_values"] if p.get("Instance") == "DIP1500_2")
    _sync_workbooks(state)
    tcp_sheet = next(s for s in state["workbooks"]["MODBUS_TCP.xlsx"]["sheets"] if s["name"] == added[0]["sheet"])
    instance_col = tcp_sheet["headers"].index("Instance")
    assert any(row[instance_col] == "DIP1500_2" for row in tcp_sheet["rows"])
    assert "DIP1500_2" in {sheet["name"] for sheet in state["workbooks"]["PARAMETERS_PA.xlsx"]["sheets"]}


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


def test_copied_type_never_calls_exchange_with_incompatible_dut_and_zero_is_preserved():
    import copy
    state = copy.deepcopy(load_pack())
    typ = copy.deepcopy(next(t for t in state["types"] if t["name"] == "DIP1500"))
    typ.update(id="DIP_COPY", name="DIP_COPY")
    state["types"].append(typ)
    member = typ["templates"]["TEMPLATE_PA"][0]["Member"]
    warnings = []
    _add_instance(state, "DIP_COPY_1", typ, {member: 0}, warnings)
    assert next(p for p in state["pa_values"] if p.get("Instance") == "DIP_COPY_1" and p.get("Member") == member)["Instance value"] == 0
    assert not any(c.get("instance_b_id") == "DIP_COPY_1" or c.get("instance_a_id") == "DIP_COPY_1" for c in state["exchange_calls"])
    assert warnings


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
        assert proposal["tcp_points"] and proposal["rtu_slaves"]
        preview=client.post("/api/v2/instances/preview",headers=headers,json={**payload,"tcp_points":[{"id":r["id"],"address":r["address"]} for r in proposal["tcp_points"]],"rtu_slaves":[{"id":r["id"],"address":r["address"]} for r in proposal["rtu_slaves"]]})
        assert preview.status_code==200 and not preview.json()["errors"]
        token=preview.json()["token"]
        applied=client.post("/api/v2/instances/apply",headers=headers,json={"token":token})
        assert applied.status_code==200
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":token}).status_code==404
        after=client.get("/api/v2/project").json()
        instance=next(i for i in after["instances"] if i["name"]=="DIP1500_2")
        assert any(p.get("Instance")=="DIP1500_2" for p in after["tcp_points"])
        config=client.get(f"/api/v2/instances/{instance['id']}/configuration").json()
        assert config["revision"]==after["revision"]
        assert all(str(p.get("GVL_MODBUS variable", "")).startswith("GVL_MODBUS.") for p in config["tcp_points"])
        first=config["tcp_points"][0]
        occupied=next(p for p in after["tcp_points"] if p.get("table")==first.get("table") and p.get("Client")==first.get("Client") and p.get("Instance")!="DIP1500_2")
        collision=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"modbus","instance_id":"DIP1500_2","tcp_points":[{"id":first["id"],"address":occupied["address"]}]})
        assert any("already in use" in e for e in collision.json()["errors"])
        moved_address=_tcp_range(first.get("Client"),first.get("table"))[1]
        moved=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"modbus","instance_id":"DIP1500_2","tcp_points":[{"id":first["id"],"address":moved_address}]})
        assert moved.status_code==200 and not moved.json()["errors"]
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":moved.json()["token"]}).status_code==200
        after=client.get("/api/v2/project").json()
        moved_row=next(p for p in after["tcp_points"] if p["id"]==first["id"])
        assert moved_row["address"]==moved_address and moved_row["GVL_MODBUS variable"]==_tcp_variable(moved_row["table"],moved_address)
        pa=config["pa_values"][0]
        invalid_pa=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"pa","instance_id":"DIP1500_2","values":{"COMMUNICATION_ADDRESS":"70000"}})
        assert any("valid WORD" in e for e in invalid_pa.json()["errors"])
        saved_pa=client.post("/api/v2/instances/preview",headers=headers,json={"revision":after["revision"],"action":"pa","instance_id":"DIP1500_2","values":{"COMMUNICATION_ADDRESS":"17"}})
        assert saved_pa.status_code==200 and not saved_pa.json()["errors"]
        assert client.post("/api/v2/instances/apply",headers=headers,json={"token":saved_pa.json()["token"]}).status_code==200
        after=client.get("/api/v2/project").json()
        channels=config["options"]["io_channels"]
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
