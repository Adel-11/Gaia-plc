import json
import sys
import types

from fastapi.testclient import TestClient

from backend.main import create_app


def install_engine(monkeypatch):
    state = {
        "settings": {"project_name": "Test", "version": "1"},
        "equipment_types": [], "equipment": [], "points": [],
        "fuxa_template": "", "fuxa_existing": [], "generation_logs": [],
    }
    engine = types.ModuleType("backend.engine")
    engine.empty_state = lambda: json.loads(json.dumps(state))
    engine.seed_state = engine.empty_state
    engine.validate = lambda value: {"errors": [], "warnings": []}
    engine.allocate_addresses = lambda value: value
    engine.import_excel = lambda data, current: json.loads(json.dumps(current))
    engine.import_fuxa = lambda data, current: {**json.loads(json.dumps(current)), "fuxa_template": data.decode("utf-8")}
    engine.export_excel = lambda current: b"excel-bytes"
    engine.export_fuxa = lambda current: b"csv-bytes\n"
    monkeypatch.setitem(sys.modules, "backend.engine", engine)


def make_client(tmp_path, monkeypatch):
    install_engine(monkeypatch)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-42")
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=False, cookie_secure=False)
    return TestClient(application)


def login(client):
    response = client.post("/api/login", json={"username": "admin", "password": "correct-horse-battery-42"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_health_and_auth_protection(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/me").status_code == 401
        token = login(client)
        assert client.get("/api/me").json() == {"username": "admin", "csrf_token": token}
        assert client.get("/api/state").status_code == 200


def test_mutations_require_csrf_and_backup_excludes_credentials(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        login(client)
        assert client.post("/api/logout").status_code == 403
        token = client.get("/api/me").json()["csrf_token"]
        response = client.put("/api/state", json={"settings": {"project_name": "Changed"}}, headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        backup = client.get("/api/backup")
        assert backup.status_code == 200
        payload = json.loads(backup.content)
        assert payload["state"]["settings"]["project_name"] == "Changed"
        assert "password_hash" not in backup.text
        assert list((tmp_path / "exports").glob("gaia-backup-*.json"))


def test_import_preview_is_persisted_and_applied_with_csrf(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        token = login(client)
        response = client.post("/api/imports/preview", files={"file": ("reference.csv", b"header\n", "text/csv")}, headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        preview = response.json()
        assert preview["state"]["fuxa_template"] == "header\n"
        assert len(list((tmp_path / "imports").glob("*.csv"))) == 1
        applied = client.post("/api/imports/apply", json={"token": preview["token"]}, headers={"X-CSRF-Token": token})
        assert applied.status_code == 200
        assert client.get("/api/state").json()["fuxa_template"] == "header\n"
        assert client.post("/api/imports/apply", json={"token": preview["token"]}, headers={"X-CSRF-Token": token}).status_code == 404


def test_login_rejects_bad_credentials(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/login", json={"username": "admin", "password": "wrong"})
        assert response.status_code == 401
        assert client.get("/api/state").status_code == 401


def test_real_seed_duplicate_export_and_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-42")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=True, cookie_secure=False)
    with TestClient(application) as client:
        token = login(client)
        state = client.get("/api/state").json()
        assert state["equipment_types"]
        assert state["equipment"]
        assert (tmp_path / "app.db").exists()
        selected_type = next(t for t in state["equipment_types"] if t["code"] == "DIP1500")
        source = next(e for e in state["equipment"] if e["type_id"] == selected_type["id"])
        before_points = len(state["points"])
        duplicate = client.post("/api/equipment", json={"source_id": source["id"]}, headers={"X-CSRF-Token": token})
        assert duplicate.status_code == 200, duplicate.text
        new_tag = duplicate.json()["equipment"]["tag"]
        assert new_tag.startswith("DIP1500_")
        assert len(duplicate.json()["points"]) > 0
        assert len(client.get("/api/state").json()["points"]) > before_points
        csv_one = client.get("/api/exports/fuxa")
        csv_two = client.get("/api/exports/fuxa")
        assert csv_one.status_code == csv_two.status_code == 200
        assert csv_one.content == csv_two.content
        assert list((tmp_path / "exports").glob("fuxa-*.csv"))
    restarted = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=True, cookie_secure=False)
    with TestClient(restarted) as client:
        saved = client.get("/api/state")
        assert saved.status_code == 401
        login(client)
        assert any(e.get("tag") == new_tag for e in client.get("/api/state").json()["equipment"])


def test_real_catalog_add_creates_valid_points_for_each_type(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-42")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=True, cookie_secure=False)
    with TestClient(application) as client:
        csrf_token = login(client)
        types = client.get("/api/state").json()["equipment_types"]
        created = []
        for equipment_type in types:
            response = client.post(
                "/api/equipment",
                json={"type_id": equipment_type["id"]},
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code == 200, f"{equipment_type['code']}: {response.text}"
            equipment = response.json()["equipment"]
            points = response.json()["points"]
            assert equipment["tag"].startswith(equipment_type["code"] + "_")
            assert equipment["codesys_names"]["gvl_io"] == f"GVL_IO.IO_{equipment['tag']}"
            assert equipment["codesys_names"]["dut_io"] == f"DUT_IO_{equipment_type['code']}"
            assert equipment["codesys_names"]["fb_instance"] == f"FB_INST_{equipment['tag']}"
            assert points, f"{equipment_type['code']} should receive its catalog point templates"
            assert all(p["equipment_id"] == equipment["id"] for p in points)
            assert all(p.get("fuxa_id", "").startswith("t_") for p in points)
            created.append((equipment_type["code"], equipment, points))
        state = client.get("/api/state").json()
        assert client.get("/api/validation").json()["errors"] == []
        for code, equipment, points in created:
            assert all(equipment["tag"] in p["plc_variable"] or equipment["tag"] in p["scada_name"] for p in points), code


def test_equipment_preview_confirm_is_reviewed_owned_and_releases_addresses(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-42")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=True, cookie_secure=False)
    with TestClient(application) as client:
        csrf_token = login(client)
        headers = {"X-CSRF-Token": csrf_token}
        state = client.get("/api/state").json()
        typ = next(t for t in state["equipment_types"] if t.get("point_templates"))
        before = json.dumps(state, sort_keys=True)

        preview = client.post("/api/equipment/preview", json={"type_id": typ["id"], "tag": "REVIEWED_1"}, headers=headers)
        assert preview.status_code == 200, preview.text
        draft = preview.json()
        assert draft["token"] and draft["points"]
        assert set(draft["free_addresses"]) == {"coils", "discrete_inputs", "input_registers", "holding_registers"}
        assert json.dumps(client.get("/api/state").json(), sort_keys=True) == before

        candidate = draft["points"][0]
        table = candidate["table"]
        picked = draft["free_addresses"][table][12]
        candidate["address"] = picked
        confirmed = client.post("/api/equipment/confirm", json={"token": draft["token"], "points": [candidate]}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["points"]) == 1
        assert confirmed.json()["points"][0]["address"] == picked
        assert client.post("/api/equipment/confirm", json={"token": draft["token"], "points": [candidate]}, headers=headers).status_code == 404

        # A full-width collision with an existing point is rejected without saving the instance.
        state = client.get("/api/state").json()
        collision = next((p for p in state["points"] if p.get("address") and p.get("table") == table), None)
        if collision:
            rejected_preview = client.post("/api/equipment/preview", json={"type_id": typ["id"], "tag": "REVIEWED_2"}, headers=headers).json()
            rejected_point = rejected_preview["points"][0]
            rejected_point["table"] = collision["table"]
            rejected_point["address"] = collision["address"]
            pre_reject = json.dumps(client.get("/api/state").json(), sort_keys=True)
            rejected = client.post("/api/equipment/confirm", json={"token": rejected_preview["token"], "points": [rejected_point]}, headers=headers)
            assert rejected.status_code == 422
            assert json.dumps(client.get("/api/state").json(), sort_keys=True) == pre_reject

        stale_preview = client.post("/api/equipment/preview", json={"type_id": typ["id"], "tag": "REVIEWED_3"}, headers=headers).json()
        changed = client.get("/api/state").json()
        changed["settings"]["version"] = "changed"
        assert client.put("/api/state", json=changed, headers=headers).status_code == 200
        stale = client.post("/api/equipment/confirm", json={"token": stale_preview["token"], "points": []}, headers=headers)
        assert stale.status_code == 409

        # Deleting the confirmed instance releases its address for the next preview.
        assert client.delete(f"/api/equipment/{confirmed.json()['equipment']['id']}", headers=headers).status_code == 200
        next_preview = client.post("/api/equipment/preview", json={"type_id": typ["id"], "tag": "REVIEWED_4"}, headers=headers).json()
        next_point = next(p for p in next_preview["points"] if p["table"] == table)
        next_point["address"] = picked
        reused = client.post("/api/equipment/confirm", json={"token": next_preview["token"], "points": [next_point]}, headers=headers)
        assert reused.status_code == 200, reused.text


def test_equipment_categories_use_distinct_namespaces_and_import_route_rejects_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-battery-42")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=True, cookie_secure=False)
    with TestClient(application) as client:
        csrf_token = login(client)
        headers = {"X-CSRF-Token": csrf_token}
        state = client.get("/api/state").json()
        typ = next(t for t in state["equipment_types"] if t.get("point_templates"))
        typ["point_templates"] = [
            {"variable_category": "SCADA", "suffix": "Pressure", "scada_name": "{instance} Pressure", "table": "coils", "codesys_type": "BOOL", "fuxa_type": "Bool", "words": 1, "direction": "Plc To SCADA", "target": "FUXA"},
            {"variable_category": "IO", "suffix": "InputOk", "scada_name": "{instance} Input OK", "table": "discrete_inputs", "codesys_type": "BOOL", "fuxa_type": "Bool", "words": 1, "direction": "Plc To SCADA", "target": "FUXA", "io_address": "%IX0.0"},
            {"variable_category": "COM", "suffix": "Flow", "scada_name": "{instance} Flow", "table": "input_registers", "codesys_type": "DINT", "fuxa_type": "Int32", "words": 1, "direction": "Plc To SCADA", "target": "FUXA", "source_address": "40001", "source_table": "holding_registers"},
        ]
        changed = client.put("/api/state", json=state, headers=headers)
        assert changed.status_code == 200, changed.text

        preview = client.post("/api/equipment/preview", json={"type_id": typ["id"], "tag": "CATTEST_1"}, headers=headers)
        assert preview.status_code == 200, preview.text
        draft = preview.json()
        # Equipment preview credentials cannot be replayed through the import-apply endpoint.
        assert client.post("/api/imports/apply", json={"token": draft["token"]}, headers=headers).status_code == 404
        by_category = {p["variable_category"]: p for p in draft["points"]}
        assert by_category["SCADA"]["plc_variable"] == "GVL_SCADA.SCADA_CATTEST_1.Pressure"
        assert by_category["IO"]["plc_variable"] == "GVL_IO.IO_CATTEST_1.InputOk"
        assert by_category["COM"]["plc_variable"] == "GVL_COM.COM_CATTEST_1.Flow"
        confirmed = client.post("/api/equipment/confirm", json={"token": draft["token"], "points": draft["points"]}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
        exported = client.get("/api/exports/fuxa")
        assert exported.status_code == 200
        csv = exported.content.decode("utf-8")
        assert "CATTEST_1 Pressure" in csv
        assert "CATTEST_1 Input OK" not in csv
        assert "CATTEST_1 Flow" not in csv


def test_first_start_requires_explicit_admin_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    application = create_app(data_dir=tmp_path, secret_key="s" * 48, seed=False, cookie_secure=False)
    try:
        with TestClient(application):
            assert False, "startup should reject an unconfigured administrator"
    except RuntimeError as exc:
        assert "ADMIN_USERNAME" in str(exc)
