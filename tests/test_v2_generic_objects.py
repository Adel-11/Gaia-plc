"""Exercise model authoring and one-time inheritance through the public API."""
from fastapi.testclient import TestClient
from lxml import etree

from backend.main import create_app


def test_generic_models_stay_out_of_plc_until_used_and_rename_bound_references(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GAIA_UPGRADE_HMI_PROJECT", "Gaia")
    monkeypatch.setenv("ADMIN_USERNAME", "generic-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "generic-test-password")
    app = create_app(data_dir=tmp_path, secret_key="generic-test-" * 4, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post("/api/login", json={"username": "generic-test", "password": "generic-test-password"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        project = client.get("/api/v2/project").json()
        original_types = project["types"]
        original_tree = client.get("/api/v2/plc/tree").json()["objects"]
        starter = next(item for item in project["generic_objects"] if item["id"] == "sensor")
        saved = client.post("/api/v2/generic-objects", headers=headers, json={
            "revision": project["revision"],
            "name": "Capteur générique avec mise à l’échelle",
            "signals": [{**starter["signals"][0], "iec_type": "REAL"}],
            "members": [
                {"id": "gain", "role": "PA", "name": "GAIN", "iec_type": "REAL", "default_value": "1.0"},
                {"id": "status", "role": "COM", "name": "HMI_OUTPUT_VALIDE", "iec_type": "BOOL"},
                {"id": "diag", "role": "IO", "name": "INPUT_DIAG", "iec_type": "BOOL"},
                {"id": "memory", "role": "INT", "name": "MEMOIRE", "iec_type": "BOOL"},
                {"id": "ready", "role": "EXC", "name": "OUTPUT_PRET", "iec_type": "BOOL"},
            ],
            "processing": "// INTERN.MESURE dans un commentaire\nINTERN.MESURE := INTERN.MESURE * PA.GAIN;",
        })
        assert saved.status_code == 200, saved.text
        model = saved.json()["template"]
        project = client.get("/api/v2/project").json()
        assert project["types"] == original_types
        assert client.get("/api/v2/plc/tree").json()["objects"] == original_tree
        assert all("GENERIC" not in book["filename"] for book in project["workbooks"])

        config = {
            "revision": project["revision"], "name": "DUAL_SENSOR", "kind": "Equipment",
            "generic": {
                "template_id": model["id"],
                "signals": [
                    {**model["signals"][0], "name": "NIVEAU", "unit": "mm"},
                    {"id": "pressure", "name": "PRESSION", "direction": "input", "kind": "analog",
                     "iec_type": "DWORD", "internal_name": "PRESSION_BRUTE", "exchange_name": "OUTPUT_PRESSION_BRUTE"},
                ],
                "members": [{**model["members"][0], "name": "GAIN_NIVEAU"}, *model["members"][1:]],
            },
        }
        review = client.post("/api/v2/types/preview", headers=headers, json=config).json()
        assert review["errors"] == [], review
        assert "INTERN.NIVEAU := IO.INPUT_NIVEAU;" in review["generated_st"]
        assert "INTERN.PRESSION_BRUTE := IO.INPUT_PRESSION;" in review["generated_st"]
        assert "EXC.OUTPUT_PRESSION_BRUTE := INTERN.PRESSION_BRUTE;" in review["generated_st"]
        assert "INTERN.NIVEAU := INTERN.NIVEAU * PA.GAIN_NIVEAU;" in review["generated_st"]
        assert "// INTERN.MESURE dans un commentaire" in review["generated_st"]
        created = client.post("/api/v2/types/apply", headers=headers, json={"token": review["token"]})
        assert created.status_code == 200, created.text
        typ = created.json()["type"]
        io_dut = next(dut for dut in typ["duts"] if dut["role"] == "IO")
        assert [member["name"] for member in io_dut["members"]] == ["INPUT_NIVEAU", "INPUT_PRESSION", "INPUT_DIAG"]
        assert {dut["role"] for dut in typ["duts"]} == {"PA", "COM", "EXC", "IO", "INT"}
        assert all(any(member["name"] == name for member in dut["members"])
                   for role, name in (("COM", "HMI_OUTPUT_VALIDE"), ("EXC", "OUTPUT_PRET"), ("INT", "MEMOIRE"))
                   for dut in typ["duts"] if dut["role"] == role)
        assert not typ["templates"]
        exported = client.post("/api/v2/plc/export/type/DUAL_SENSOR", headers=headers)
        assert exported.status_code == 200, exported.text
        names = set(etree.fromstring(exported.content).xpath('//*[local-name()="Object"]/@Name'))
        assert "FB_INST_DUAL_SENSOR" in names
        assert not any("GENERIC" in name for name in names)
        project = client.get("/api/v2/project").json()
        assert len(project["instances"]) == 4
        assert next(item for item in project["generic_objects"] if item["id"] == model["id"]) == model

        pump = next(item for item in project["generic_objects"] if item["id"] == "pump")
        config.update(revision=project["revision"], name="TRANSFER_PUMP", generic={
            "template_id": pump["id"],
            "signals": [row for row in pump["signals"] if row["name"] != "AUTORISATION"],
            "members": pump["members"],
        })
        blocked = client.post("/api/v2/types/preview", headers=headers, json=config).json()
        assert blocked["token"] is None
        assert "INTERN.AUTORISATION" in blocked["errors"][0]
        config["generic"]["inherit_processing"] = False
        allowed = client.post("/api/v2/types/preview", headers=headers, json=config).json()
        assert allowed["errors"] == [], allowed
        before = client.get("/api/v2/project").json()
        removed = client.delete(f"/api/v2/generic-objects/{model['id']}", params={"revision": before["revision"]}, headers=headers)
        assert removed.status_code == 200, removed.text
        after = client.get("/api/v2/project").json()
        assert all(item["id"] != model["id"] for item in after["generic_objects"])
        assert any(item["name"] == "DUAL_SENSOR" for item in after["types"])
