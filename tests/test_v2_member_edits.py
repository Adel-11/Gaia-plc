"""A type edit updates the model and the PLC program without touching unrelated code."""
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app


def test_member_edits_and_local_vars_reach_export(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GAIA_UPGRADE_HMI_PROJECT", "Gaia")
    monkeypatch.setenv("ADMIN_USERNAME", "member-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "member-test-password")
    app = create_app(data_dir=tmp_path, secret_key="member-test-" * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post("/api/login", json={"username": "member-test", "password": "member-test-password"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        def review(path, data):
            response = client.post(f"/api/v2/{path}", headers=headers, json=data)
            assert response.status_code == 200, response.text
            return response.json()

        def apply(area, preview):
            assert preview.get("token") and not preview.get("errors"), preview
            response = client.post(f"/api/v2/{area}/apply", headers=headers, json={"token": preview["token"]})
            assert response.status_code == 200, response.text
            return response.json()

        def project():
            return client.get("/api/v2/project").json()

        template = next(t for t in project()["generic_objects"] if t["id"] == "sensor")
        created = apply("types", review("types/preview", {
            "revision": project()["revision"], "name": "QA_SIGNAL", "kind": "Equipment",
            "generic": {"template_id": "sensor", "signals": template["signals"], "members": [
                {"id": "counter", "role": "COM", "name": "HMI_OUTPUT_COUNT", "iec_type": "WORD"}
            ]},
        }))["type"]
        obj_path = "/api/v2/objects/type/QA_SIGNAL"
        obj = client.get(obj_path).json()
        assert "IO.INPUT_MESURE" in obj["fb"]["generated_body"]
        duts = obj["duts"]
        io = next(d for d in duts if d["role"] == "IO")
        io["members"][0]["name"] = "INPUT_HEIGHT"
        com = next(d for d in duts if d["role"] == "COM")
        com["members"].append({"name": "HMI_OUTPUT_READY", "iec_type": "BOOL"})
        apply("objects", review("objects/type/QA_SIGNAL/preview", {
            "revision": obj["revision"], "duts": duts,
            "processing_code": "// IO.INPUT_MESURE is an old label\nLocalTimer(IN := TRUE);\n",
            "local_variables": [{"name": "LocalTimer", "iec_type": "TON"}],
        }))
        obj = client.get(obj_path).json()
        assert "INTERN.MESURE := IO.INPUT_HEIGHT;" in obj["fb"]["generated_body"]
        assert "// IO.INPUT_MESURE is an old label" in obj["fb"]["generated_body"]
        assert "LocalTimer : TON" in obj["fb"]["declaration"]
        assert any(o["path"] == "COM.HMI_OUTPUT_READY" for o in obj["fb"]["variable_options"])

        export = client.post("/api/v2/plc/export/type/QA_SIGNAL", headers=headers)
        assert export.status_code == 200, export.text
        xml = etree.fromstring(export.content)
        assert xml.xpath('//*[local-name()="localVars"]/*[@name="LocalTimer"]//*[local-name()="derived"]/@name') == ["TON"]

        # A reviewed CODESYS round trip does not duplicate generator-managed VARs.
        from backend.v2_codesys import preview_codesys
        from backend.v2_plc import build_program
        from backend import v2_store
        with app.state.session_factory() as session:
            current = v2_store.load_project(session)
        imported = preview_codesys(export.content, "qa-signal.xml", current)
        assert not imported["errors"], imported["errors"]
        state = imported["state"]
        fb = build_program(state)[created["fb"]["name"]]["element"]
        assert len(fb.xpath('//*[local-name()="localVars"]/*[@name="LocalTimer"]')) == 1

        obj = client.get(obj_path).json()
        result = review("objects/type/QA_SIGNAL/preview", {"revision": obj["revision"], "local_variables": []})
        assert not result["token"] and any("LocalTimer" in e for e in result["errors"])

        # Copy a PLC type and remove its only IO without creating any instance links.
        copied = apply("types", review("types/preview", {
            "revision": obj["revision"], "name": "QA_COPY", "kind": "Equipment", "copy_from": "QA_SIGNAL",
            "duts": [{**d, "members": []} if d["role"] == "IO" else d for d in obj["duts"]],
        }))["type"]
        assert "LocalTimer" in [row["name"] for row in copied["fb"]["local_variables"]]
        assert "IO.INPUT_HEIGHT" not in copied["fb"]["body"].replace("// IO.INPUT_MESURE is an old label", "")

        # Removing the last IO member drops the generated link and keeps a valid DUT.
        obj = client.get(obj_path).json()
        next(d for d in obj["duts"] if d["role"] == "IO")["members"] = []
        apply("objects", review("objects/type/QA_SIGNAL/preview", {"revision": obj["revision"], "duts": obj["duts"]}))
        obj = client.get(obj_path).json()
        assert "INTERN.MESURE := IO.INPUT_HEIGHT;" not in obj["fb"]["generated_body"]
        assert [m["name"] for d in obj["duts"] if d["role"] == "IO" for m in d["members"]] == ["SPARE"]

        apply("instances", review("instances/preview", {
            "revision": project()["revision"], "action": "create", "name": "QA_SIGNAL_1", "type_id": "QA_SIGNAL",
        }))
        config = client.get("/api/v2/configuration").json()
        tcp = next(row for row in config["tcp_clients"] if row["Client"] == "HMI")
        cap = config["tcp_capacity"]["holding_registers"]
        bounds = tcp["ranges"]["holding_registers"]
        occupied = {r["address"] for r in config["tcp_points"] if r["table"] == "holding_registers"}
        address = next(n for n in range(max(cap[0], bounds[0]), min(cap[1], bounds[1])+1) if n not in occupied)
        apply("configuration", review("configuration/preview", {
            "revision": config["revision"], "changes": [{"collection": "tcp_points", "op": "create", "values": {
                "Instance": "QA_SIGNAL_1", "Client": tcp["Client"], "Member": "HMI_OUTPUT_COUNT",
                "Table": "holding_registers", "Modbus address": address, "Direction": "PLC to client",
            }}],
        }))
        obj = client.get(obj_path).json()
        com = next(d for d in obj["duts"] if d["role"] == "COM")
        next(m for m in com["members"] if m["name"] == "HMI_OUTPUT_COUNT")["name"] = "HMI_OUTPUT_TOTAL"
        apply("objects", review("objects/type/QA_SIGNAL/preview", {"revision": obj["revision"], "duts": obj["duts"]}))
        config = client.get("/api/v2/configuration").json()
        linked = next(row for row in config["tcp_points"] if row["instance_id"] == "QA_SIGNAL_1")
        assert linked["GVL_COM variable"] == "GVL_COM.COM_QA_SIGNAL_1.HMI_OUTPUT_TOTAL"
        assert linked["address"] == address
        obj = client.get(obj_path).json()
        com = next(d for d in obj["duts"] if d["role"] == "COM")
        com["members"] = [m for m in com["members"] if m["name"] != "HMI_OUTPUT_TOTAL"]
        apply("objects", review("objects/type/QA_SIGNAL/preview", {"revision": obj["revision"], "duts": obj["duts"]}))
        config = client.get("/api/v2/configuration").json()
        assert not any(row["instance_id"] == "QA_SIGNAL_1" for row in config["tcp_points"])

def test_exchange_renames_only_the_affected_parameter_and_its_conditions():
    from backend.v2_member_sync import sync_consumers

    typ = {"id": "A", "name": "A", "duts": []}
    fn = {"id": "FC_EXC_A_B", "name": "FC_EXC_A_B", "sheet": "A-B",
          "type_a_id": "A", "type_b_id": "B", "parameter_a": "EXC_A", "parameter_b": "EXC_B",
          "mappings": [{
              "id": "map", "source": "EXC_A.READY", "destination": "EXC_B.READY",
              "condition": {"mode": "all", "terms": [
                  {"left": "EXC_B.READY", "operator": "=", "right": {"kind": "variable", "value": "EXC_A.READY"}}
              ]},
              "else_value": {"kind": "literal", "value": "FALSE"},
          }]}
    state = {"types": [typ], "exchange_functions": [fn], "exchange_calls": []}
    errors, changes = sync_consumers(state, typ, {"EXC": {"ready": "AVAILABLE"}})
    assert not errors and changes
    row = fn["mappings"][0]
    assert (row["source"], row["destination"]) == ("EXC_A.AVAILABLE", "EXC_B.READY")
    assert row["condition"]["terms"][0]["right"]["value"] == "EXC_A.AVAILABLE"
    errors, changes = sync_consumers(state, typ, {"EXC": {"available": None}})
    assert not errors and changes and not fn["mappings"]
    assert state["exchange_functions"] == [fn]
