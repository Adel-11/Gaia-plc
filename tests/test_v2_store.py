from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from backend.db import Base
from backend import v2_store
from backend.v2_engine import load_pack


def _engine():
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return engine


def _state():
    return {
        "types": [{
            "id": "Motor", "name": "Motor", "kind": "Equipment",
            "duts": [{"id": "DUT_COM", "name": "DUT_COM", "role": "COM", "members": [
                {"id": "COM_INPUT_RUN", "position": 0, "section": "Input", "name": "COM_INPUT_RUN", "iec_type": "BOOL"},
            ]}],
            "fb": {"name": "FB_INST_Motor", "declaration": "VAR_IN_OUT", "body": "exact user ST"},
        }],
        "instances": [{"id": "Motor_1", "name": "Motor_1", "type_id": "Motor"}],
        "tcp_clients": [{"id": "FUXA", "name": "FUXA"}],
        "tcp_points": [{"id": "coils:FUXA:1", "name": "Run", "client_id": "FUXA", "instance_id": "Motor_1", "table": "coils", "address": 1}],
        "workbooks": {"TYPE_Motor.xlsx": {"sheets": [{"name": "DUT_COM", "headers": ["Member"], "rows": [["COM_INPUT_RUN"]]}]}},
        "snapshot": {"filename": "latest.xml", "xml": "<project/>", "object_ids": {"PLC_PRG": "oid-1"}},
        "custom_extension": {"preserve": True},
    }


def test_roundtrip_normalizes_relations_and_keeps_full_revision_history():
    engine = _engine()
    state = _state()
    with Session(engine, expire_on_commit=False) as session:
        first = v2_store.initialize(session, state)
        assert first["revision"] == 1
        session.commit()

    with Session(engine, expire_on_commit=False) as session:
        loaded = v2_store.load_project(session)
        assert loaded["custom_extension"] == {"preserve": True}
        assert loaded["types"][0]["fb"]["body"] == "exact user ST"
        assert session.scalar(select(func.count()).select_from(v2_store.V2DutMember)) == 1
        assert session.scalar(select(func.count()).select_from(v2_store.V2TcpPoint)) == 1
        assert session.get(v2_store.V2CodesysSnapshot, 1).payload
        second = v2_store.save_project(session, loaded, username="adel", expected_revision=1)
        assert second["revision"] == 2
        session.commit()

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(v2_store.V2ProjectRevision)) == 2
        assert v2_store.initialize(session, {"types": []})["revision"] == 2


def test_save_rejects_stale_revision_without_adding_a_revision():
    engine = _engine()
    with Session(engine) as session:
        v2_store.initialize(session, _state())
        session.commit()
    with Session(engine) as session:
        try:
            v2_store.save_project(session, _state(), expected_revision=0)
        except ValueError as exc:
            assert "expected 0, current is 1" in str(exc)
        else:
            raise AssertionError("stale expected_revision should be rejected")
        assert session.scalar(select(func.count()).select_from(v2_store.V2ProjectRevision)) == 1


def test_reference_pack_load_initialize_and_reimport_save_with_foreign_keys():
    engine = _engine()
    state = load_pack()
    assert state["types"] and state["instances"] and state["pa_values"]
    with Session(engine, expire_on_commit=False) as session:
        initialized = v2_store.initialize(session, state)
        assert initialized["revision"] == 1
        session.commit()

    with Session(engine, expire_on_commit=False) as session:
        loaded = v2_store.load_project(session)
        assert loaded["workbooks"].keys() == state["workbooks"].keys()
        assert len(loaded["types"]) == len(state["types"])
        assert len(loaded["pa_values"]) == len(state["pa_values"])
        saved = v2_store.save_project(session, loaded, username="adel", expected_revision=1)
        assert saved["revision"] == 2
        session.commit()

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(v2_store.V2ProjectRevision)) == 2
