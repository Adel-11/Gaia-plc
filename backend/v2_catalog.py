"""Shared member catalogs and reviewed virtual I/O authoring."""
from __future__ import annotations

import copy
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from backend.db import ImportPreview

BASE_SECTIONS = ["INPUT (field to plc)", "OUTPUT (plc to field)", "STATE", "PARAMETERS", "INTERNAL"]
BASE_PREFIXES = ["INPUT", "OUTPUT", "STATE", "CMD", "SPARE", "COM", "HMI", "SCADA"]
IEC_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IEC_TYPES = {"BOOL", "BYTE", "WORD", "DWORD", "LWORD", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "REAL", "LREAL", "TIME", "DATE", "TIME_OF_DAY", "DATE_AND_TIME"}


def _io_type(iec_type, direction):
    digital = str(iec_type).upper() == "BOOL"
    return ("DI" if digital else "AI") if direction == "input" else ("DO" if digital else "AO")


def _compatible_virtual_io_type(io_type, iec_type):
    if io_type in {"AI", "AO"}:
        return str(iec_type).upper() != "BOOL"
    return str(iec_type).upper() == "BOOL"


class CatalogOptions(BaseModel):
    sections: list[str]
    prefixes: list[str]


class IOPreview(BaseModel):
    revision: int
    action: str
    id: str | None = None
    name: str | None = None
    iec_type: str | None = None
    direction: str | None = None
    instance_id: str | None = None
    member_name: str | None = None


class Apply(BaseModel):
    token: str


def _catalog(state):
    options = state.get("catalog_options") or {}
    return {"sections": list(options.get("sections") or BASE_SECTIONS),
            "prefixes": list(options.get("prefixes") or BASE_PREFIXES)}


def _identifier(value):
    return bool(IEC_IDENTIFIER.fullmatch(str(value or ""))) and str(value).upper() not in {"TRUE", "FALSE", "VAR", "TYPE", "STRUCT", "END_TYPE", "END_STRUCT"}


def _virtual_io_name(row):
    return str(row.get("name") or row.get("id") or "")


def _io_target_in_use(state, target, excluding_id=None):
    target_key = str(target or "").casefold()
    if not target_key:
        return False
    for collection in (state.get("io_channels", []), state.get("virtual_io", [])):
        for row in collection:
            if excluding_id is not None and str(row.get("id")) == str(excluding_id):
                continue
            member = row.get("GVL_IO member")
            if member and str(member).casefold() == target_key:
                return True
    return False


def register_catalog_routes(app, get_session, current_user, csrf):
    from backend import v2_store as store

    def project_for(session):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "The v2 project has not been initialized")
        return state

    @app.get("/api/v2/catalog/options")
    def get_catalog(session=Depends(get_session), user=Depends(current_user)):
        return _catalog(project_for(session))

    @app.post("/api/v2/catalog/options")
    def set_catalog(body: CatalogOptions, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        clean = {}
        for key, values in (("sections", body.sections), ("prefixes", body.prefixes)):
            normalized = [str(value).strip() for value in values if str(value).strip()]
            if not normalized or any(len(value) > 64 for value in normalized):
                raise HTTPException(422, f"{key.title()} must contain short non-empty labels")
            if len({value.casefold() for value in normalized}) != len(normalized):
                raise HTTPException(422, f"{key.title()} cannot contain duplicates")
            clean[key] = normalized
        state = copy.deepcopy(current)
        state["catalog_options"] = clean
        try:
            saved = store.save_project(session, state, username=getattr(user, "username", "admin"), expected_revision=current.get("revision", 0))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.commit()
        return {**clean, "revision": saved["revision"]}

    @app.post("/api/v2/io/preview")
    def preview_io(body: IOPreview, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        current = project_for(session)
        if body.revision != current.get("revision", 0):
            raise HTTPException(409, "Project revision changed; reload I/O")
        state = copy.deepcopy(current)
        rows = state.setdefault("virtual_io", [])
        errors, changes = [], []
        action = body.action
        row = next((r for r in rows if str(r.get("id")) == str(body.id)), None) if body.id else None
        try:
            if action == "create":
                name = str(body.name or "").strip()
                iec_type = str(body.iec_type or "").strip().upper()
                direction = str(body.direction or "").strip().lower()
                if not _identifier(name): raise ValueError("Name must be a valid IEC identifier")
                if iec_type not in IEC_TYPES: raise ValueError("Choose a supported IEC scalar type")
                if direction not in {"input", "output"}: raise ValueError("Direction must be input or output")
                if any(_virtual_io_name(r).casefold() == name.casefold() for r in rows): raise ValueError("Virtual I/O name already exists")
                io_type = _io_type(iec_type, direction)
                rid = "VIO_" + secrets.token_hex(8).upper()
                row = {"id": rid, "name": name, "iec_type": iec_type, "Type": iec_type,
                       "direction": direction, "I/O type": io_type, "IOMAP type": iec_type,
                       "Module": "GVL_IO", "Slot": "VIRTUAL", "Channel": rid,
                       "GVL_IOMAP variable": f"GVL_IOMAP.IOMAP_{io_type}_{name}",
                       "variable": f"GVL_IOMAP.IOMAP_{io_type}_{name}", "Status": "VIRTUAL"}
                if any(str(r.get("GVL_IOMAP variable") or r.get("variable") or "").casefold() == row["GVL_IOMAP variable"].casefold() for r in state.get("io_channels", [])):
                    raise ValueError("The generated GVL_IOMAP variable is already used by a physical channel")
                rows.append(row)
                changes.append(f"Create virtual {direction} {name} : {iec_type}")
            elif action in {"connect", "disconnect"}:
                if not row: raise ValueError("Virtual I/O channel not found")
                if action == "disconnect":
                    if not row.get("instance_id"): raise ValueError("Virtual I/O channel is not connected")
                    old = row.get("instance_id"); row.pop("instance_id", None); row.pop("Instance", None); row.pop("member_name", None); row.pop("GVL_IO member", None)
                    changes.append(f"Disconnect virtual I/O {row['name']} from {old}")
                else:
                    instance = next((i for i in state.get("instances", []) if str(i.get("id", i.get("name"))) == str(body.instance_id) or str(i.get("name")) == str(body.instance_id)), None)
                    if not instance: raise ValueError("Instance not found")
                    if not body.member_name: raise ValueError("Choose an I/O member")
                    typ = next((t for t in state.get("types", []) if str(t.get("id", t.get("name"))) == str(instance.get("type_id")) or str(t.get("name")) == str(instance.get("type_id"))), None)
                    dut = next((d for d in (typ or {}).get("duts", []) if d.get("role") == "IO"), None)
                    member = next((m for m in (dut or {}).get("members", []) if str(m.get("name")) == body.member_name), None)
                    if not member: raise ValueError("Choose a member from this instance's DUT_IO")
                    target = f"GVL_IO.IO_{instance.get('name', instance.get('id'))}.{body.member_name}"
                    if _io_target_in_use(state, target, excluding_id=row["id"]):
                        raise ValueError("DUT_IO member is already connected to a physical or virtual I/O channel")
                    if any(str(r.get("id")) != str(row["id"]) and str(r.get("instance_id")) == str(instance.get("name", instance.get("id"))) and str(r.get("member_name")) == body.member_name for r in rows):
                        raise ValueError("DUT_IO member is already connected to another channel")
                    iec = str(member.get("iec_type", "")).upper()
                    if not _compatible_virtual_io_type(row["I/O type"], iec):
                        raise ValueError(f"{body.member_name} is not compatible with {row['I/O type']}")
                    if re.sub(r"\s+", "", iec).upper() != re.sub(r"\s+", "", str(row.get("IOMAP type") or row.get("iec_type") or "")).upper():
                        raise ValueError(f"{body.member_name} type {iec} must match virtual channel type {row.get('iec_type')}")
                    instance_name = str(instance.get("name") or instance.get("id"))
                    row.update(instance_id=instance_name, Instance=instance_name, member_name=body.member_name,
                               **{"GVL_IO member": target, "Status": "VIRTUAL"})
                    changes.append(f"Connect virtual I/O {row['name']} to {instance_name}.IO.{body.member_name}")
            else: raise ValueError("Action must be create, connect, or disconnect")
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
        if errors: return {"token": None, "errors": errors, "warnings": [], "changes": changes, "virtual_io": rows}
        warnings = []
        try:
            from backend.v2_plc import build_program, serialize_program
            from backend.v2_plc_validation import validate_xml
            built = build_program(state)
            checked = validate_xml(serialize_program(built, state, list(built)), state, built)
            old_built = build_program(current)
            baseline = validate_xml(serialize_program(old_built, current, list(old_built)), current, old_built)
            old_errors = set(baseline.get("errors", []))
            errors.extend(error for error in checked.get("errors", []) if error not in old_errors)
            warnings.extend(checked.get("warnings", []))
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"PLC consistency check failed: {exc}")
        if errors: return {"token": None, "errors": errors, "warnings": warnings, "changes": changes, "virtual_io": rows}
        token = secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token, owner_id=user.id, state_payload=json.dumps(state, ensure_ascii=False),
            diff_payload=json.dumps({"kind": "v2-virtual-io", "base_revision": current.get("revision", 0)}, ensure_ascii=False),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
        session.commit()
        return {"token": token, "errors": [], "warnings": warnings, "changes": changes, "virtual_io": rows}

    @app.post("/api/v2/io/apply")
    def apply_io(body: Apply, session=Depends(get_session), user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview and preview.expires_at.tzinfo is None else (preview.expires_at if preview else now)
        if not preview or preview.owner_id != user.id or expiry <= now:
            raise HTTPException(404, "I/O preview not found or expired")
        meta = json.loads(preview.diff_payload)
        if meta.get("kind") != "v2-virtual-io": raise HTTPException(404, "I/O preview not found or expired")
        current = project_for(session)
        if current.get("revision", 0) != meta.get("base_revision"):
            raise HTTPException(409, "Project changed after preview; create a new preview")
        state = json.loads(preview.state_payload)
        try:
            saved = store.save_project(session, state, username=getattr(user, "username", "admin"), expected_revision=meta["base_revision"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.delete(preview)
        session.commit()
        return {"revision": saved["revision"]}
