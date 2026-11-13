"""PanelView reference acceptance and scoped V7 archive migration."""
from collections import Counter
from copy import deepcopy
import io
import zipfile

from fastapi.testclient import TestClient
from lxml import etree
import openpyxl

from backend.main import create_app
from backend.v2_addressing import allocate_tcp_points, client_ranges
from backend.v2_codesys import _ids, _objects, _st
from backend.v2_engine import export_workbook, import_workbook, validate_project
from backend.v2_fb_flow import convert_body, convert_project, render_flow
from backend.v2_hmi import PROFILE, REFERENCE, RETIRED, load_hmi_pack
from backend.v2_panelview_export import export_panelview
from backend.v2_plc import build_program, serialize_program
from backend.v2_plc_validation import validate_xml
from tests.test_v2_plc_acceptance import _dut_signature, _gvl_signature, _interface_signature, _st_tokens


def test_panelview_program_and_workbooks_match_the_codesys_reference():
    state = deepcopy(load_hmi_pack())
    assert {t["name"] for t in state["types"]} == {"ADM100W", "DIP1500", "DOSENSOR", "UPI_LOCAL_HMI"}
    assert len(state["instances"]) == 4
    assert len(state["exchange_calls"]) == 3
    assert [client["Client"] for client in state["tcp_clients"]] == ["HMI"]
    counts = Counter(row["table"] for row in state["tcp_points"])
    assert counts == {"coils": 15, "discrete_inputs": 16, "input_registers": 99, "holding_registers": 13}
    assert set(row["Modbus address"] for row in state["tcp_points"] if row["table"] == "discrete_inputs") == (
        set(range(10001, 10021)) - {10013, 10015, 10017, 10019}
    )
    assert client_ranges(state, "HMI")["coils"][0] == 1
    assert validate_project(state)["errors"] == []

    root = etree.parse(str(REFERENCE)).getroot()
    expected = _objects(root)
    # Production persisted the previous conversion before export.
    for typ in state['types']:
        fb = typ['fb']
        fb['flow'] = convert_body(fb['body'], legacy_order=True)
        fb['body'] = render_flow(fb['flow'])
    assert convert_project(state)
    assert not convert_project(state)
    ids, id_errors = _ids(root, expected)
    assert not id_errors
    objects = build_program(state)
    xml = serialize_program(objects, state, list(objects))
    assert validate_xml(xml, state, objects)["errors"] == []
    output = etree.fromstring(xml)
    actual = _objects(output)
    assert len(actual) == 41
    assert set(actual) == set(expected)
    assert _ids(output, actual) == (ids, [])
    checks = {"dataType": [_dut_signature], "globalVars": [_gvl_signature],
              "pou": [_interface_signature, _st_tokens]}
    for name, node in expected.items():
        for signature in checks[etree.QName(node).localname]:
            assert signature(actual[name]) == signature(node), (name, signature.__name__)
        if etree.QName(node).localname == "pou":
            assert _st(actual[name]) == _st(node), name

    for filename in state["workbooks"]:
        content = export_workbook(state, filename)
        preview = import_workbook(content, filename, state)
        assert preview["validation"]["errors"] == [], filename
        assert not any(preview["diff"].values()), filename

    # A free HMI map can be allocated without any paired supervision client.
    state["tcp_points"] = []
    points = allocate_tcp_points(state, "DOSENSOR_1", "HMI")
    assert all(point["Client"] == "HMI" for point in points)
    assert min(point["Modbus address"] for point in points if point["table"] == "coils") == 1


def test_flow_repair_preserves_edits_made_after_the_old_conversion():
    state = deepcopy(load_hmi_pack())
    fb = next(t['fb'] for t in state['types'] if t['name'] == 'DIP1500')
    fb['flow'] = convert_body(fb['body'], legacy_order=True)
    fb['flow']['processing_code'] += '\nINTERN.CMD_RUN := FALSE;\n'
    edited = fb['body'] = render_flow(fb['flow'])
    convert_project(state)
    assert fb['body'] == edited


def test_panelview_external_tags_use_current_addresses_and_one_tag_per_pair():
    state = deepcopy(load_hmi_pack())
    pair = [point for point in state["tcp_points"] if point["member_name"] == "HMI_OUTPUT_FREQUENCY"]
    for point, address, word in zip(pair, (501, 502), ("MSW", "LSW")):
        point.update({"Modbus address": address, "Word": word})
    rows = openpyxl.load_workbook(io.BytesIO(export_panelview(state, "PLC-2")))["External Tags"]
    assert rows.max_column == 14 and rows.max_row == 94
    assert tuple(cell.value for cell in rows[2][:4]) == ("(Tag Name)", "(Data Type)", "(Address)", "(Controller)")
    tags = {row[0]: row for row in list(rows.values)[2:]}
    assert tags["ADM100W_1_HMI_OUTPUT_FREQUENCY"][:4] == ("ADM100W_1_HMI_OUTPUT_FREQUENCY", "Real", "30501", "PLC-2")
    assert tags["DOSENSOR_1_HMI_INPUT_CALIBRATION_HIGH"][1:3] == ("Boolean", "1")
    assert tags["DOSENSOR_1_HMI_OUTPUT_FAULT"][1:3] == ("Boolean", "10001")
    assert tags["DIP1500_1_HMI_INPUT_MOTOR_SPEED"][1:3] == ("Real", "40001")


def test_startup_upgrade_archives_gaia_and_preserves_other_projects(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GAIA_UPGRADE_HMI_PROJECT", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "panelview-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "panelview-test-password")
    settings = {"data_dir": tmp_path, "secret_key": "panelview-test-key-" * 3, "cookie_secure": False}
    credentials = {"username": "panelview-test", "password": "panelview-test-password"}
    with TestClient(create_app(**settings)) as client:
        csrf = {"X-CSRF-Token": client.post("/api/login", json=credentials).json()["csrf_token"]}
        gaia = client.get("/api/v2/project").json()
        other = client.post("/api/v2/workspace/projects", headers=csrf, json={
            "name": "Other PLC project", "copy_current": True,
        }).json()
        other_before = client.get("/api/v2/project").json()

    monkeypatch.setenv("GAIA_UPGRADE_HMI_PROJECT", "Gaia")
    with TestClient(create_app(**settings)) as client:
        csrf = {"X-CSRF-Token": client.post("/api/login", json=credentials).json()["csrf_token"]}
        upgraded = client.get("/api/v2/project").json()
        assert upgraded["project_id"] == gaia["project_id"]
        assert upgraded["program_profile"] == PROFILE
        assert upgraded["validation"]["errors"] == []
        assert client.get("/api/v2/exports/fuxa.csv").status_code == 404
        hmi = client.get("/api/v2/exports/panelview.xlsx")
        assert hmi.status_code == 200 and "VARIABLES_HMI.xlsx" in hmi.headers["content-disposition"]
        assert openpyxl.load_workbook(io.BytesIO(hmi.content)).sheetnames == ["External Tags"]
        archive = client.get("/api/v2/workspace/archive-v7").json()["state"]
        assert archive["revision"] == gaia["revision"]
        assert archive["types"] == gaia["types"]
        assert archive["tcp_points"] == gaia["tcp_points"]
        review = client.post("/api/v2/plc/preview", headers=csrf, json={"mode": "full"}).json()
        assert review["errors"] == []
        assert len(review["manual_deletions"]) == 19
        assert any("PanelView 800" in action for action in review["manual_actions"])

        template_zip = client.get("/api/v2/templates.zip")
        assert template_zip.status_code == 200
        with zipfile.ZipFile(io.BytesIO(template_zip.content)) as bundle:
            for name in bundle.namelist():
                book = openpyxl.load_workbook(io.BytesIO(bundle.read(name)))
                assert any(sheet.data_validations.dataValidation for sheet in book)
                for sheet in book:
                    for row in sheet.iter_rows():
                        for cell in row:
                            assert not RETIRED.search(str(cell.value))
                            assert cell.comment is None or not RETIRED.search(cell.comment.text)
        assert client.post("/api/v2/workspace/select", headers=csrf, json={"id": other["id"]}).status_code == 200
        assert client.get("/api/v2/project").json() == other_before
        assert client.get("/api/v2/workspace/archive-v7").status_code == 404

    with TestClient(create_app(**settings)) as client:
        login = client.post("/api/login", json=credentials)
        assert login.status_code == 200
        assert client.get("/api/v2/project").json()["revision"] == upgraded["revision"]
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        history = client.get("/api/v2/workspace").json()["history"]
        original_version = next(entry for entry in history if entry["version"] == gaia["version"])
        preview = client.post("/api/v2/workspace/history/restore/preview", headers=csrf, json={
            "revision": upgraded["revision"], "target_revision": original_version["revision"],
        }).json()
        assert any("Gaia V7" in change for change in preview["changes"])
        restored = client.post("/api/v2/workspace/restore/apply", headers=csrf, json={"token": preview["token"]})
        assert restored.status_code == 200, restored.text
        assert client.get("/api/v2/project").json()["types"] == gaia["types"]
        from backend.v2_hmi import upgrade_named_project
        with client.app.state.session_factory() as session:
            assert upgrade_named_project(session, "Gaia") is False
