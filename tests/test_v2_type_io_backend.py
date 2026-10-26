import copy

from backend.v2_engine import load_pack, export_workbook, import_workbook
from backend.v2_fb_flow import convert_body, flow_view, render_flow, convert_project
from backend.v2_plc import build_program, HNS, NS
from backend.v2_type_creation import _prepare_upi_bindings
from backend.v2_instance_editor import _connect_bound_upis, _clear_virtual_io_for_instance
from backend.v2_catalog import _io_target_in_use, _io_type, _compatible_virtual_io_type, IEC_TYPES
from backend.v2_exchanges import compile_function
from fastapi.testclient import TestClient
from backend.main import create_app


def test_legacy_input_and_output_logic_moves_into_editable_processing():
    source = "// INPUTS\nINTERN.A := COM.A;\nIF COM.EN THEN\n INTERN.B := TRUE;\nEND_IF\n// PROCESSING\nINTERN.C := INTERN.B;\n// OUTPUTS\nCOM.OUT := INTERN.C;\nINTERN.D := INTERN.C + 1;\n"
    flow = convert_body(source)
    view = flow_view({"body": source, "flow": flow})
    assert view["processing_code"] == "IF COM.EN THEN\n INTERN.B := TRUE;\nEND_IF\nINTERN.C := INTERN.B;\nINTERN.D := INTERN.C + 1;\n"
    assert "preserved_sections" not in view
    assert view["migrations"]
    assert len(view["mappings"]) == 2
    assert "INTERN.A := COM.A;" in view["generated_body"]
    assert "COM.OUT := INTERN.C;" in view["generated_body"]


def test_virtual_io_model_generates_gvl_iomap_and_io_copy():
    state = copy.deepcopy(load_pack())
    state["virtual_io"] = [{
        "id": "VIO_TEST_1", "name": "TEST_SENSOR", "iec_type": "DWORD", "Type": "DWORD",
        "I/O type": "AI", "IOMAP type": "DWORD", "direction": "input",
        "GVL_IOMAP variable": "GVL_IOMAP.IOMAP_AI_TEST_SENSOR",
        "instance_id": "DOSENSOR_1", "Instance": "DOSENSOR_1", "member_name": "INPUT_SIGNAL",
        "GVL_IO member": "GVL_IO.IO_DOSENSOR_1.INPUT_SIGNAL", "Status": "VIRTUAL"
    }]
    program = build_program(state)
    iomap = program["GVL_IOMAP"]["element"]
    assert any(v.get("name") == "IOMAP_AI_TEST_SENSOR" for v in iomap.findall(NS + "variable"))
    io_in = program["GFC_IO_IN"]["element"].find(NS + "body/" + NS + "ST/" + HNS + "xhtml")
    assert "GVL_IO.IO_DOSENSOR_1.INPUT_SIGNAL := GVL_IOMAP.IOMAP_AI_TEST_SENSOR;" in (io_in.text or "")


def test_virtual_io_rejects_target_already_driven_by_physical_channel():
    target = "GVL_IO.IO_PUMP_1.INPUT_SIGNAL"
    state = {"io_channels": [{"id": "PHYS_1", "GVL_IO member": target}], "virtual_io": []}
    assert _io_target_in_use(state, target)
    assert _io_target_in_use(state, target, excluding_id="VIO_1")


def test_every_supported_virtual_io_type_has_a_compatible_directional_class():
    for iec_type in IEC_TYPES:
        for direction, prefix in (("input", "DI" if iec_type == "BOOL" else "AI"),
                                  ("output", "DO" if iec_type == "BOOL" else "AO")):
            io_type = _io_type(iec_type, direction)
            assert io_type == prefix
            assert _compatible_virtual_io_type(io_type, iec_type)


def test_deleting_instance_clears_virtual_io_target_link():
    state = {"virtual_io": [{"id": "VIO_1", "name": "SENSOR", "instance_id": "PUMP_1",
                             "Instance": "PUMP_1", "member_name": "INPUT_SIGNAL",
                             "GVL_IO member": "GVL_IO.IO_PUMP_1.INPUT_SIGNAL", "Status": "VIRTUAL"}]}
    _clear_virtual_io_for_instance(state, "PUMP_1")
    row = state["virtual_io"][0]
    assert row["Status"] == "VIRTUAL"
    assert not any(field in row for field in ("instance_id", "Instance", "member_name", "GVL_IO member"))


def test_upi_binding_materializes_missing_exc_members_and_typed_function():
    equipment = {"id": "PUMP", "name": "PUMP", "kind": "Equipment", "duts": [
        {"id": "PUMP:EXC", "name": "DUT_EXC_PUMP", "role": "EXC", "members": []}], "templates": {}}
    upi = {"id": "UPI_DRIVE", "name": "UPI_DRIVE", "kind": "Functional unit (UPI)",
           "source_file": "TYPE_UPI_DRIVE.xlsx", "duts": [
        {"id": "UPI_DRIVE:EXC", "name": "DUT_EXC_UPI_DRIVE", "role": "EXC", "members": []}], "templates": {}}
    state = {"types": [equipment, upi], "exchange_functions": [], "workbooks": {
        "TYPE_UPI_DRIVE.xlsx": {"sheets": [{"name": "DUT_EXC", "headers": ["Section", "Member", "Type", "Default value", "Unit", "Comment", "Exchange"], "rows": []}]}
    }}
    errors, bindings = _prepare_upi_bindings(state, equipment, [{
        "upi_type_id": "UPI_DRIVE",
        "mappings": [{"source": "equipment.CMD_START", "destination": "upi.CMD_START", "iec_type": "BOOL"}]
    }])
    assert errors == []
    assert bindings[0]["exchange_function_id"] == "FC_EXC_PUMP_UPI_DRIVE"
    assert equipment["duts"][0]["members"][0]["name"] == "CMD_START"
    assert upi["duts"][0]["members"][0]["name"] == "CMD_START"
    upi_rows = state["workbooks"]["TYPE_UPI_DRIVE.xlsx"]["sheets"][0]["rows"]
    assert upi_rows == [[None, "CMD_START", "BOOL", None, None, None, None]]
    function = state["exchange_functions"][0]
    assert function["mappings"] == [{"source": "EXC_PUMP.CMD_START", "destination": "EXC_UPI_DRIVE.CMD_START"}]
    assert "EXC_UPI_DRIVE.CMD_START := EXC_PUMP.CMD_START;" in compile_function(state, function)


def test_upi_binding_reports_unknown_members_instead_of_dropping_mapping():
    equipment = {"id": "PUMP", "name": "PUMP", "kind": "Equipment", "duts": [
        {"id": "PUMP:EXC", "name": "DUT_EXC_PUMP", "role": "EXC", "members": []}], "templates": {}}
    upi = {"id": "UPI_DRIVE", "name": "UPI_DRIVE", "kind": "Functional unit (UPI)", "duts": [
        {"id": "UPI_DRIVE:EXC", "name": "DUT_EXC_UPI_DRIVE", "role": "EXC", "members": []}], "templates": {}}
    state = {"types": [equipment, upi], "exchange_functions": []}
    errors, bindings = _prepare_upi_bindings(state, equipment, [{
        "upi_type_id": "UPI_DRIVE", "mappings": [{"source": "equipment.MISSING", "destination": "upi.MISSING"}]
    }])
    assert errors and "does not exist" in " ".join(errors)
    assert bindings == []


def test_reverse_upi_control_mapping_creates_equipment_member_and_compiles_direction():
    equipment = {"id": "PUMP", "name": "PUMP", "kind": "Equipment", "duts": [
        {"id": "PUMP:EXC", "name": "DUT_EXC_PUMP", "role": "EXC", "members": []}], "templates": {}}
    upi = {"id": "UPI_DRIVE", "name": "UPI_DRIVE", "kind": "Functional unit (UPI)", "duts": [
        {"id": "UPI_DRIVE:EXC", "name": "DUT_EXC_UPI_DRIVE", "role": "EXC", "members": [
            {"id": "UPI_DRIVE:EXC:0", "position": 0, "name": "STATE_ON", "iec_type": "BOOL"}]}], "templates": {}}
    state = {"types": [equipment, upi], "exchange_functions": []}
    errors, bindings = _prepare_upi_bindings(state, equipment, [{
        "upi_type_id": "UPI_DRIVE",
        "mappings": [{"source": "upi.STATE_ON", "destination": "equipment.CMD_ENABLE"}]
    }])
    assert errors == []
    assert bindings[0]["mappings"] == [{"source": "upi.STATE_ON", "destination": "equipment.CMD_ENABLE"}]
    assert equipment["duts"][0]["members"][0]["name"] == "CMD_ENABLE"
    function = state["exchange_functions"][0]
    assert function["mappings"] == [{"source": "EXC_UPI_DRIVE.STATE_ON", "destination": "EXC_PUMP.CMD_ENABLE"}]
    assert "EXC_PUMP.CMD_ENABLE := EXC_UPI_DRIVE.STATE_ON;" in compile_function(state, function)


def test_existing_serialized_flow_with_preserved_sections_is_migrated_at_startup():
    flow = {
        "version": 1, "prefix": "", "input_header": "// INPUTS\n",
        "input_segments": [{"kind": "preserved", "code": "INTERN.LEGACY_INPUT := COM.VALUE;\n"}],
        "processing_header": "// PROCESSING\n", "processing_code": "INTERN.CALC := INTERN.LEGACY_INPUT;\n",
        "output_header": "// OUTPUTS\n",
        "output_segments": [{"kind": "preserved", "code": "COM.LEGACY_OUTPUT := INTERN.CALC;\n"}],
    }
    body = render_flow(flow)
    state = {"types": [{"id": "T", "name": "T", "fb": {"body": body, "flow": flow}}]}
    assert render_flow(flow) == body
    assert convert_project(state)
    migrated = state["types"][0]["fb"]
    assert migrated["flow"]["processing_code"] == "INTERN.LEGACY_INPUT := COM.VALUE;\nINTERN.CALC := INTERN.LEGACY_INPUT;\nCOM.LEGACY_OUTPUT := INTERN.CALC;\n"
    assert "preserved" not in repr(migrated["flow"]["input_segments"] + migrated["flow"]["output_segments"])
    assert migrated["body"].count("INTERN.LEGACY_INPUT := COM.VALUE;") == 1
    assert migrated["body"].count("COM.LEGACY_OUTPUT := INTERN.CALC;") == 1
    assert not convert_project(state)


def test_upi_instance_pairing_works_in_both_creation_orders_and_is_idempotent():
    equipment_type = {"id": "PUMP", "name": "PUMP", "kind": "Equipment", "upi_bindings": [{
        "upi_type_id": "UPI_DRIVE", "exchange_function_id": "FC_EXC_PUMP_UPI_DRIVE",
        "mappings": [{"source": "equipment.CMD", "destination": "upi.CMD"}]
    }], "duts": [{"id": "PUMP:EXC", "name": "DUT_EXC_PUMP", "role": "EXC", "members": [
        {"name": "CMD", "iec_type": "BOOL", "position": 0}
    ]}]}
    upi_type = {"id": "UPI_DRIVE", "name": "UPI_DRIVE", "kind": "Functional unit (UPI)", "duts": [{
        "id": "UPI_DRIVE:EXC", "name": "DUT_EXC_UPI_DRIVE", "role": "EXC", "members": [
            {"name": "CMD", "iec_type": "BOOL", "position": 0}
        ]
    }]}
    base = {"types": [equipment_type, upi_type], "exchange_calls": [], "exchange_functions": []}

    equipment_added_second = {**copy.deepcopy(base), "instances": [
        {"id": "UPI_DRIVE_1", "name": "UPI_DRIVE_1", "type_id": "UPI_DRIVE"},
        {"id": "PUMP_1", "name": "PUMP_1", "type_id": "PUMP"}]}
    _connect_bound_upis(equipment_added_second)
    _connect_bound_upis(equipment_added_second)
    assert [(c["instance_a_id"], c["instance_b_id"]) for c in equipment_added_second["exchange_calls"]] == [("PUMP_1", "UPI_DRIVE_1")]

    upi_added_second = {**copy.deepcopy(base), "instances": [
        {"id": "PUMP_2", "name": "PUMP_2", "type_id": "PUMP"},
        {"id": "UPI_DRIVE_2", "name": "UPI_DRIVE_2", "type_id": "UPI_DRIVE"}]}
    _connect_bound_upis(upi_added_second)
    assert [(c["instance_a_id"], c["instance_b_id"]) for c in upi_added_second["exchange_calls"]] == [("PUMP_2", "UPI_DRIVE_2")]


def test_shared_upi_types_materialize_project_local_exchange_function_on_instance_creation():
    equipment_type = {"id": "PUMP", "name": "PUMP", "kind": "Equipment", "upi_bindings": [{
        "upi_type_id": "UPI_DRIVE", "mappings": [
            {"source": "equipment.CMD_ENABLE", "destination": "upi.STATE_ENABLE"}
        ], "exchange_function_id": "FC_EXC_PUMP_UPI_DRIVE"
    }], "duts": [{"id": "PUMP:EXC", "name": "DUT_EXC_PUMP", "role": "EXC", "members": [
        {"id": "PUMP:EXC:0", "position": 0, "name": "CMD_ENABLE", "iec_type": "BOOL"}
    ]}], "templates": {}}
    upi_type = {"id": "UPI_DRIVE", "name": "UPI_DRIVE", "kind": "Functional unit (UPI)", "duts": [{
        "id": "UPI_DRIVE:EXC", "name": "DUT_EXC_UPI_DRIVE", "role": "EXC", "members": [
            {"id": "UPI_DRIVE:EXC:0", "position": 0, "name": "STATE_ENABLE", "iec_type": "BOOL"}
        ]
    }], "templates": {}}
    state = {
        "types": [equipment_type, upi_type],
        "instances": [
            {"id": "PUMP_1", "name": "PUMP_1", "type_id": "PUMP"},
            {"id": "UPI_1", "name": "UPI_1", "type_id": "UPI_DRIVE"}
        ],
        "exchange_functions": [], "exchange_calls": [], "workbooks": {
            "EXCHANGES_EXC.xlsx": {"sheets": [
                {"name": "FC_EXC", "headers": ["FC", "Object type A", "Object type B", "Custom code"], "rows": []},
                {"name": "CALLS", "headers": ["FC", "Instance A", "Instance B", "Parameter A value", "Parameter B value"], "rows": []}
            ]}
        }
    }

    _connect_bound_upis(state)

    assert len(state["exchange_functions"]) == 1
    function = state["exchange_functions"][0]
    assert function["id"] == "FC_EXC_PUMP_UPI_DRIVE"
    assert function["mappings"][0]["source"] == "EXC_PUMP.CMD_ENABLE"
    assert function["mappings"][0]["destination"] == "EXC_UPI_DRIVE.STATE_ENABLE"
    assert "EXC_UPI_DRIVE.STATE_ENABLE := EXC_PUMP.CMD_ENABLE;" in compile_function(state, function)
    assert len(state["exchange_calls"]) == 1
    assert state["exchange_calls"][0]["FC"] == "FC_EXC_PUMP_UPI_DRIVE"
    workbook = state["workbooks"]["EXCHANGES_EXC.xlsx"]
    assert workbook["sheets"][0]["rows"][0][0] == "FC_EXC_PUMP_UPI_DRIVE"
    assert any(sheet["name"] == "PUMP-UPI_DRIVE" and sheet["rows"] for sheet in workbook["sheets"])


def test_catalog_and_virtual_io_review_apply_persists_and_connects(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "catalog-author")
    monkeypatch.setenv("ADMIN_PASSWORD", "catalog-author-password")
    app = create_app(data_dir=tmp_path, secret_key="catalog-api-tests-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        login = client.post("/api/login", json={"username": "catalog-author", "password": "catalog-author-password"}).json()
        headers = {"X-CSRF-Token": login["csrf_token"]}
        options = client.get("/api/v2/catalog/options").json()
        assert "INPUT (field to plc)" in options["sections"]
        assert "SCADA" in options["prefixes"]
        saved_options = client.post("/api/v2/catalog/options", headers=headers, json={
            "sections": ["INPUT (field to plc)", "OUTPUT (plc to field)", "STATE", "PARAMETERS", "INTERNAL", "DIAGNOSTICS"],
            "prefixes": ["INPUT", "OUTPUT", "STATE", "CMD", "SPARE", "COM", "HMI", "SCADA", "PLC"]
        })
        assert saved_options.status_code == 200, saved_options.text
        assert "DIAGNOSTICS" in client.get("/api/v2/catalog/options").json()["sections"]

        state = client.get("/api/v2/project").json()
        created = client.post("/api/v2/io/preview", headers=headers, json={
            "revision": state["revision"], "action": "create", "name": "TEST_LIMIT", "iec_type": "BOOL", "direction": "input"
        }).json()
        assert not created["errors"] and created["token"]
        assert client.post("/api/v2/io/apply", headers=headers, json={"token": created["token"]}).status_code == 200

        state = client.get("/api/v2/project").json()
        virtual = next(row for row in state["virtual_io"] if row["name"] == "TEST_LIMIT")
        connected = client.post("/api/v2/io/preview", headers=headers, json={
            "revision": state["revision"], "action": "connect", "id": virtual["id"],
            "direction": "input", "instance_id": "ADM100W_1", "member_name": "SPARE"
        }).json()
        assert not connected["errors"] and connected["token"]
        assert client.post("/api/v2/io/apply", headers=headers, json={"token": connected["token"]}).status_code == 200
        updated = client.get("/api/v2/configuration").json()
        final = next(row for row in updated["virtual_io"] if row["id"] == virtual["id"])
        assert final["GVL_IO member"] == "GVL_IO.IO_ADM100W_1.SPARE"


def test_non_exchange_workbook_import_preserves_upi_binding_recipe():
    state = copy.deepcopy(load_pack())
    equipment = next(t for t in state["types"] if t.get("kind") == "Equipment")
    equipment["upi_bindings"] = [{"upi_type_id": "UPI_TEST", "mappings": [
        {"source": "equipment.CMD_RUN", "destination": "upi.CMD_RUN"}
    ], "exchange_function_id": "FC_EXC_TEST"}]
    blob = export_workbook(state, "PARAMETERS_PA.xlsx")

    restored = import_workbook(blob, "PARAMETERS_PA.xlsx", state, mode="replace")["state"]
    restored_type = next(t for t in restored["types"] if t.get("id") == equipment.get("id"))
    assert restored_type["upi_bindings"] == equipment["upi_bindings"]
