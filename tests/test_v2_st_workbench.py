"""Regression coverage for Modbus signals and explicit, reversible manual ST."""
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app


def test_transport_signals_inheritance_and_manual_st_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GAIA_UPGRADE_HMI_PROJECT", "Gaia")
    monkeypatch.setenv("ADMIN_USERNAME", "workbench-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "workbench-test-password")
    app = create_app(data_dir=tmp_path, secret_key="workbench-test-" * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post("/api/login", json={"username": "workbench-test", "password": "workbench-test-password"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        def post(path, data):
            response = client.post("/api/v2/" + path, json=data, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()

        def project():
            return client.get("/api/v2/project").json()

        def preview_apply(area, data):
            preview = post(area + "/preview", data)
            assert preview.get("token") and not preview.get("errors"), preview
            return post(area.split("/")[0] + "/apply", {"token": preview["token"]})

        rtu = {"id": "rtu", "name": "PRESSION", "direction": "input", "kind": "analog", "iec_type": "REAL", "transport": "rtu"}
        tcp = {"id": "tcp", "name": "MARCHE", "direction": "output", "kind": "digital", "iec_type": "BOOL", "transport": "tcp"}
        physical = {"id": "io", "name": "DEFAUT", "direction": "input", "kind": "digital", "iec_type": "BOOL"}
        before_preview = project()["revision"]
        preview = post("types/variables/preview", {"signals": [physical, rtu, tcp]})
        members = {d["role"]: {m["name"]: m["iec_type"] for m in d["members"]} for d in preview["duts"]}
        assert members["IO"] == {"INPUT_DEFAUT": "BOOL"}
        assert members["COM"] == {"RTU_INPUT_PRESSION": "DUT_Union_Real_Word", "TCP_OUTPUT_MARCHE": "BOOL"}
        assert members["INT"]["PRESSION"] == "REAL"
        assert members["EXC"] == {"OUTPUT_DEFAUT": "BOOL", "OUTPUT_PRESSION": "REAL"}
        assert project()["revision"] == before_preview
        parent = next(t for t in project()["generic_objects"] if t["id"] == "hmi-local")
        child = post("generic-objects", {
            "revision": project()["revision"], "name": "QA IHM", "parent_id": parent["id"],
            "processing": parent["processing"], "signals": [rtu],
            "members": [{**row, "name": "HMI_INPUT_COMMANDE"} if row["id"] == "start-request" else row for row in parent["members"]],
        })["template"]
        assert child["parent_name"] == parent["name"]
        assert "INTERN.MODE_LOCAL AND COM.HMI_INPUT_COMMANDE;" in child["processing"]
        made = preview_apply("types", {
            "revision": project()["revision"], "name": "QA_HMI_RTU", "kind": "Equipment",
            "generic": {"template_id": child["id"], "signals": child["signals"], "members": child["members"]},
        })["type"]
        assert "INTERN.PRESSION := COM.RTU_INPUT_PRESSION.Real_value;" in made["fb"]["body"]
        com = next(d for d in made["duts"] if d["role"] == "COM")
        assert next(row for row in com["members"] if row["name"] == "RTU_INPUT_PRESSION")["iec_type"] == "DUT_Union_Real_Word"

        made = preview_apply("types", {
            "revision": project()["revision"], "name": "QA_TCP", "kind": "Equipment", "signals": [tcp],
        })["type"]
        assert "COM.TCP_OUTPUT_MARCHE := INTERN.MARCHE;" in made["fb"]["body"]
        assert not any(row["name"] in {"QA_TCP", "QA_HMI_RTU"} for row in project()["instances"])

        edit = "objects/type/QA_TCP"
        obj = client.get("/api/v2/" + edit).json()
        manual_output = "COM.TCP_OUTPUT_MARCHE := FALSE;"
        preview_apply(edit, {"revision": obj["revision"], "section_overrides": {"outputs": manual_output}})
        obj = client.get("/api/v2/" + edit).json()
        assert obj["fb"]["flow"]["section_overrides"]["outputs"] == manual_output
        assert manual_output in obj["fb"]["generated_body"]
        assert "COM.TCP_OUTPUT_MARCHE := INTERN.MARCHE;" not in obj["fb"]["generated_body"]
        preview_apply(edit, {"revision": obj["revision"], "section_overrides": {"outputs": None}})
        obj = client.get("/api/v2/" + edit).json()
        assert "COM.TCP_OUTPUT_MARCHE := INTERN.MARCHE;" in obj["fb"]["generated_body"]

        source_path = "code/source/DUT_COM_QA_TCP"
        source = client.get("/api/v2/" + source_path).json()
        new_declaration = source["declaration"].replace("END_STRUCT", "    EXTRA : BOOL;\nEND_STRUCT")
        saved = post(source_path, {"revision": source["revision"], "declaration": new_declaration})
        assert saved["manual_declaration"] and saved["declaration"] == new_declaration
        exported = client.post("/api/v2/plc/export/type/QA_TCP", headers=headers)
        assert exported.status_code == 200, exported.text
        root = etree.fromstring(exported.content)
        plain = root.xpath('//*[local-name()="dataType" and @name="DUT_COM_QA_TCP"]//*[local-name()="InterfaceAsPlainText"]/*/text()')
        assert plain == [new_declaration]

        program_path = "code/source/PLC_PRG"
        source = client.get("/api/v2/" + program_path).json()
        manual = post(program_path, {"revision": source["revision"], "body": source["body"] + "\n// Manually authored"})
        assert manual["manual_body"] and manual["body"].endswith("// Manually authored")
        restored = post(program_path, {"revision": manual["revision"], "body": None})
        assert not restored["manual_body"] and restored["body"] == source["body"]
