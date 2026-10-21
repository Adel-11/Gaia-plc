"""FastAPI application for the Gaia IO Generator."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from functools import lru_cache

from alembic import command
from alembic.config import Config
from pwdlib import PasswordHash
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from backend import db
from backend.db import ImportPreview, User

MAX_IMPORT_BYTES = 20 * 1024 * 1024
PREVIEW_TTL_HOURS = 2
SESSION_MAX_AGE = 12 * 60 * 60
_login_attempts: dict[str, list[float]] = {}
_password_hasher = PasswordHash.recommended()


@lru_cache(maxsize=1)
def _v2_reference_project():
    from backend.v2_engine import load_pack
    return load_pack()


def _backup_legacy_before_v2(engine, data_dir: Path):
    """Keep the legacy database, including users, before the first v2 migration."""
    import sqlite3
    from sqlalchemy import inspect
    tables = inspect(engine).get_table_names()
    if "project_state" not in tables:
        return
    with engine.connect() as connection:
        legacy = connection.execute(text("SELECT payload FROM project_state WHERE id=1")).scalar()
        if not legacy:
            return
        if "v2_project" in tables and connection.execute(text("SELECT COUNT(*) FROM v2_project")).scalar():
            return
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(exist_ok=True)
    json_backup = backup_dir / "legacy-v1-before-v2.json"
    if not json_backup.exists():
        json_backup.write_text(legacy, encoding="utf-8")
    sqlite_backup = backup_dir / "legacy-v1-before-v2.sqlite3"
    if engine.dialect.name == "sqlite" and not sqlite_backup.exists():
        raw = engine.raw_connection()
        try:
            with sqlite3.connect(sqlite_backup) as target:
                raw.driver_connection.backup(target)
        finally:
            raw.close()
    archive_path = backup_dir / "legacy-v1-before-v2.tar.gz"
    if not archive_path.exists():
        import tarfile
        temporary = archive_path.with_suffix(".tmp")
        with tarfile.open(temporary, "w:gz", dereference=False) as archive:
            if sqlite_backup.exists():
                archive.add(sqlite_backup, arcname="app.db")
            for child in data_dir.iterdir():
                if child.name == "backups" or child.name.startswith("app.db"):
                    continue
                archive.add(child, arcname=child.name)
        temporary.replace(archive_path)


class Credentials(BaseModel):
    username: str
    password: str


class StateBody(BaseModel):
    model_config = {"extra": "allow"}


class PreviewApply(BaseModel):
    token: str


class EquipmentBody(BaseModel):
    type_id: str | None = None
    tag: str | None = None
    source_id: str | None = None
    description: str = ""


class EquipmentPreviewBody(BaseModel):
    type_id: str | None = None
    tag: str | None = None
    source_id: str | None = None


class EquipmentConfirmBody(BaseModel):
    token: str
    points: list[dict[str, Any]]


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"added": [], "modified": [], "removed": []}
    for collection in ("equipment_types", "equipment", "points"):
        before = {str(x.get("id")): x for x in old.get(collection, []) if isinstance(x, dict)}
        after = {str(x.get("id")): x for x in new.get(collection, []) if isinstance(x, dict)}
        for key in after.keys() - before.keys():
            result["added"].append({"collection": collection, "id": key, "item": after[key]})
        for key in before.keys() - after.keys():
            result["removed"].append({"collection": collection, "id": key, "item": before[key]})
        for key in before.keys() & after.keys():
            if before[key] != after[key]:
                result["modified"].append({"collection": collection, "id": key, "before": before[key], "after": after[key]})
    if old.get("settings") != new.get("settings"):
        result["modified"].append({"collection": "settings", "before": old.get("settings"), "after": new.get("settings")})
    for group in result.values():
        group.sort(key=lambda x: (x.get("collection", ""), x.get("id", "")))
    return result


def _store_file(data_dir: Path, subdir: str, filename: str, content: bytes) -> Path:
    target_dir = data_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    # Use a server-generated filename so client paths can never escape the data directory.
    target = target_dir / filename
    target.write_bytes(content)
    return target


def _allocate_name(code: str, equipment: list[dict[str, Any]]) -> str:
    used = {x.get("tag") for x in equipment}
    index = 1
    while f"{code}_{index}" in used:
        index += 1
    return f"{code}_{index}"


def _rename_instance(value: Any, code: str, tag: str) -> Any:
    if not isinstance(value, str):
        return value
    value = value.replace("{instance}", tag).replace("{tag}", tag)
    value = re.sub(rf"(?<![A-Za-z0-9]){re.escape(code)}_\d+(?![A-Za-z0-9])", tag, value)
    return value


def _codesys_names(code: str, tag: str) -> dict[str, str]:
    return {
        "gvl_io": f"GVL_IO.IO_{tag}", "dut_io": f"DUT_IO_{code}",
        "gvl_com": f"GVL_COM.COM_{tag}", "dut_com": f"DUT_COM_{code}",
        "gvl_exc": f"GVL_EXC.EXC_{tag}", "dut_exc": f"DUT_EXC_{code}",
        "gvl_scada": f"GVL_SCADA.SCADA_{tag}", "dut_scada": f"DUT_SCADA_{code}",
        "fb_instance": f"FB_INST_{tag}",
    }


def _state_hash(state: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _build_equipment_candidate(state: dict[str, Any], body: EquipmentBody | EquipmentPreviewBody):
    """Build a detached equipment and point set. This never mutates project state."""
    from copy import deepcopy
    types = {str(x.get("id")): x for x in state.get("equipment_types", [])}
    source = next((x for x in state.get("equipment", []) if x.get("id") == body.source_id), None) if body.source_id else None
    type_id = str(source.get("type_id")) if source else body.type_id
    equipment_type = types.get(str(type_id))
    if not equipment_type:
        raise HTTPException(422, "Unknown equipment type")
    code = str(equipment_type.get("code") or "EQUIPMENT")
    tag = body.tag or _allocate_name(code, state.get("equipment", []))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(tag)):
        raise HTTPException(422, "Invalid equipment tag")
    if any(str(x.get("tag", "")).casefold() == str(tag).casefold() for x in state.get("equipment", [])):
        raise HTTPException(409, "Equipment tag already exists")
    new_id = str(uuid.uuid4())
    if source:
        equipment = deepcopy(source)
        old_tag = source.get("tag", "")
        equipment.update({"id": new_id, "tag": tag,
                          "description": getattr(body, "description", "") or source.get("description", ""),
                          "codesys_names": _codesys_names(code, tag)})
        points = []
        for source_point in state.get("points", []):
            if source_point.get("equipment_id") != source["id"]:
                continue
            point = deepcopy(source_point)
            point["id"] = str(uuid.uuid4())
            point["equipment_id"] = new_id
            for field in ("plc_variable", "scada_name", "hmi_address"):
                value = point.get(field)
                if isinstance(value, str):
                    point[field] = _rename_instance(value.replace(old_tag, tag), code, tag)
                    if field == "scada_name" and code in point[field] and tag not in point[field]:
                        point[field] = point[field].replace(code, tag)
                    elif field == "scada_name" and point[field].strip() and tag not in point[field]:
                        point[field] = f"{tag} {point[field]}"
            point["locked"] = False
            point["address"] = None
            point["fuxa_id"] = "t_" + uuid.uuid4().hex[:8] + "-" + uuid.uuid4().hex[:8]
            points.append(point)
    else:
        equipment = {"id": new_id, "tag": tag, "type_id": type_id,
                     "description": getattr(body, "description", "") or equipment_type.get("description", ""),
                     "active": True, "status": "planned",
                     "communication": deepcopy(equipment_type.get("defaults", {})), "notes": "",
                     "codesys_names": _codesys_names(code, tag)}
        points = []
        type_map = state.get("settings", {}).get("type_map", {})
        from backend.engine import FUXA_TYPES
        for template in equipment_type.get("point_templates", []):
            point = deepcopy(template)
            suffix = point.get("suffix") or "Point"
            point.pop("suffix", None)
            category = str(point.get("variable_category") or "SCADA").upper()
            if category not in {"SCADA", "IO", "COM"}:
                category = "SCADA"
            point["variable_category"] = category
            point["id"] = str(uuid.uuid4())
            point["equipment_id"] = new_id
            for field in ("plc_variable", "scada_name", "hmi_address"):
                value = point.get(field)
                point[field] = _rename_instance(value, code, tag)
                if field == "scada_name" and isinstance(point[field], str) and code in point[field] and tag not in point[field]:
                    point[field] = point[field].replace(code, tag)
                elif field == "scada_name" and isinstance(point[field], str) and point[field].strip() and tag not in point[field]:
                    point[field] = f"{tag} {point[field]}"
            if isinstance(point.get("plc_variable"), str):
                target_prefix = {"SCADA": "GVL_SCADA.SCADA_", "IO": "GVL_IO.IO_", "COM": "GVL_COM.COM_"}[category]
                point["plc_variable"] = re.sub(r"^GVL_(?:SCADA|IO|EXC|COM)\.(?:SCADA|IO|EXC|COM)_", target_prefix, point["plc_variable"])
            if not point.get("plc_variable"):
                prefix = {"SCADA": "GVL_SCADA.SCADA", "IO": "GVL_IO.IO", "COM": "GVL_COM.COM"}[category]
                suffix = str(suffix).replace(" ", "_")
                point["plc_variable"] = f"{prefix}_{tag}.{suffix}"
            if not str(point.get("scada_name") or "").strip():
                suffix = str(point.get("plc_variable") or "point").rsplit(".", 1)[-1].replace("_", " ").strip()
                point["scada_name"] = f"{tag} {suffix or 'Point'}"
            code_type = str(point.get("codesys_type") or "REAL").upper()
            point.setdefault("codesys_type", code_type)
            point["words"] = 2 if code_type in {"REAL", "DINT", "UDINT", "DWORD"} else 1
            point.setdefault("fuxa_type", type_map.get(code_type, FUXA_TYPES.get(code_type, "")))
            if not point.get("fuxa_id"):
                point["fuxa_id"] = "t_" + uuid.uuid4().hex[:8] + "-" + uuid.uuid4().hex[:8]
            point.setdefault("locked", False)
            points.append(point)
    return equipment, points


def _free_address_options(state: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, list[int]]:
    from backend.engine import TABLES
    existing = list(state.get("points", []))
    result: dict[str, list[int]] = {}
    for table in TABLES:
        occupied: set[int] = set()
        for point in existing:
            if point.get("table") == table and point.get("address") is not None:
                try:
                    occupied.update(range(int(point["address"]), int(point["address"]) + max(1, int(point.get("words") or 1))))
                except (TypeError, ValueError):
                    continue
        # Keep the response compact while giving the editor a useful set of starts
        # that can hold any supported point width (including two-register values).
        capacity = 504 if table in {"coils", "discrete_inputs"} else 501
        # The UI knows each selected point's width and can filter starts accordingly.
        result[table] = [address for address in range(1, 65536)
                         if address not in occupied][:capacity]
    return result


def create_app(*, data_dir: str | Path | None = None, database_url: str | None = None,
               secret_key: str | None = None, seed: bool = True,
               cookie_secure: bool | None = None, static_dir: str | Path | None = None) -> FastAPI:
    resolved_data = Path(data_dir or os.environ.get("DATA_DIR", "/data")).expanduser().resolve()
    resolved_db_url = database_url or os.environ.get("DATABASE_URL") or f"sqlite:///{(resolved_data / 'app.db').as_posix()}"
    app_secret = secret_key or os.environ.get("SECRET_KEY")
    cookie_is_secure = _env_bool("COOKIE_SECURE", True) if cookie_secure is None else cookie_secure
    alembic_path = Path(__file__).resolve().parent.parent / "alembic.ini"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not app_secret or len(app_secret) < 32:
            raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters.")
        resolved_data.mkdir(parents=True, exist_ok=True)
        for child in ("imports", "exports"):
            (resolved_data / child).mkdir(parents=True, exist_ok=True)
        engine = db.make_engine(resolved_db_url)
        app.state.engine = engine
        app.state.session_factory = db.make_session_factory(engine)
        try:
            from backend import v2_store
            if seed:
                _backup_legacy_before_v2(engine, resolved_data)
            config = Config(str(alembic_path))
            config.set_main_option("sqlalchemy.url", resolved_db_url.replace("%", "%%"))
            command.upgrade(config, "head")
            with app.state.session_factory() as session:
                db.bootstrap_admin(session, os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD"))
                session.flush()
                if session.scalar(select(User.id).limit(1)) is None:
                    raise RuntimeError("No administrator exists. Set ADMIN_USERNAME and ADMIN_PASSWORD before first startup.")
                initial = db.load_state(session)
                if initial is None:
                    from backend.engine import seed_state, empty_state
                    initial = seed_state() if seed else empty_state()
                type_by_id = {str(item.get("id")): item for item in initial.get("equipment_types", [])}
                changed_names = False
                for item in initial.get("equipment", []):
                    expected = _codesys_names(str(type_by_id.get(str(item.get("type_id")), {}).get("code", "EQUIPMENT")), str(item.get("tag", "EQUIPMENT_1")))
                    existing = item.get("codesys_names") if isinstance(item.get("codesys_names"), dict) else {}
                    completed = dict(existing)
                    for key, value in expected.items():
                        if not completed.get(key):
                            completed[key] = value
                    if completed != existing:
                        item["codesys_names"] = completed
                        changed_names = True
                if changed_names or db.load_state(session) is None:
                    db.save_state(session, initial)
                if seed and v2_store.load_project(session) is None:
                    from copy import deepcopy
                    v2_initial = deepcopy(_v2_reference_project())
                    v2_initial["migration"] = {
                        "source": "reference-v2 pack",
                        "legacy_backup": "backups/legacy-v1-before-v2.sqlite3" if (resolved_data / "backups/legacy-v1-before-v2.sqlite3").exists() else None,
                        "users_preserved": True,
                        "legacy_files_backup": "backups/legacy-v1-before-v2.tar.gz" if (resolved_data / "backups/legacy-v1-before-v2.tar.gz").exists() else None,
                    }
                    if v2_initial.get("snapshot"):
                        v2_initial["snapshot"].setdefault("imported_at", datetime.now(timezone.utc).isoformat())
                    v2_store.initialize(session, v2_initial)
                session.commit()
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Gaia IO Generator", version="0.1.0", lifespan=lifespan)
    app.state.data_dir = resolved_data
    app.add_middleware(
        SessionMiddleware,
        secret_key=app_secret or secrets.token_urlsafe(48),
        session_cookie="gaia_session",
        max_age=SESSION_MAX_AGE,
        same_site="strict",
        https_only=cookie_is_secure,
    )

    def get_session():
        factory = getattr(app.state, "session_factory", None)
        if factory is None:
            raise HTTPException(503, "Database is not ready")
        with factory() as session:
            yield session

    def current_user(request: Request, session: Session = Depends(get_session)) -> User:
        user_id = request.session.get("user_id")
        if not user_id:
            raise HTTPException(401, "Authentication required")
        user = session.get(User, user_id)
        if user is None:
            request.session.clear()
            raise HTTPException(401, "Authentication required")
        request.state.user = user
        return user

    def csrf(request: Request):
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token")
        if not expected or not supplied or not secrets.compare_digest(str(expected), supplied):
            raise HTTPException(403, "Missing or invalid CSRF token")

    def state_for(session: Session) -> dict[str, Any]:
        state = db.load_state(session)
        if state is None:
            raise HTTPException(503, "Project state is not initialized")
        return state

    @app.get("/api/health")
    def health(session: Session = Depends(get_session)):
        try:
            session.execute(text("SELECT 1"))
            return {"status": "ok"}
        except SQLAlchemyError as exc:
            raise HTTPException(503, "Database unavailable") from exc

    @app.post("/api/login")
    def login(body: Credentials, request: Request, session: Session = Depends(get_session)):
        now = time.monotonic()
        key = f"{request.client.host if request.client else 'unknown'}:{body.username.casefold()}"
        recent = [stamp for stamp in _login_attempts.get(key, []) if now - stamp < 900]
        if len(recent) >= 10:
            raise HTTPException(429, "Too many login attempts. Try again in 15 minutes.")
        user = session.scalar(select(User).where(User.username == body.username))
        valid = False
        if user:
            try:
                valid = _password_hasher.verify(body.password, user.password_hash)
            except (ValueError, TypeError):
                valid = False
        if not valid:
            recent.append(now)
            _login_attempts[key] = recent
            raise HTTPException(401, "Invalid username or password")
        _login_attempts.pop(key, None)
        request.session.clear()
        token = secrets.token_urlsafe(32)
        request.session.update({"user_id": user.id, "csrf_token": token})
        return {"username": user.username, "csrf_token": token}

    @app.post("/api/logout")
    def logout(request: Request, user: User = Depends(current_user), _: None = Depends(csrf)):
        request.session.clear()
        return {"status": "ok"}

    @app.get("/api/me")
    def me(request: Request, user: User = Depends(current_user)):
        return {"username": user.username, "csrf_token": request.session["csrf_token"]}

    @app.get("/api/state")
    def get_state(session: Session = Depends(get_session), user: User = Depends(current_user)):
        return state_for(session)

    @app.put("/api/state")
    def put_state(body: dict[str, Any], session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        from backend.engine import validate
        outcome = validate(body)
        if outcome.get("errors"):
            raise HTTPException(422, detail=outcome)
        db.save_state(session, body)
        session.commit()
        return {"state": body, "validation": outcome}

    @app.get("/api/validation")
    def validation(session: Session = Depends(get_session), user: User = Depends(current_user)):
        from backend.engine import validate
        return validate(state_for(session))

    @app.post("/api/equipment/preview")
    def preview_equipment(body: EquipmentPreviewBody, session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        from backend.engine import allocate_addresses, validate
        state = state_for(session)
        equipment, points = _build_equipment_candidate(state, body)
        proposed = {**state, "equipment": [*state.get("equipment", []), equipment], "points": [*state.get("points", []), *points]}
        try:
            allocated = allocate_addresses(proposed)
        except Exception as exc:
            raise HTTPException(422, f"Unable to suggest addresses: {exc}") from exc
        by_id = {p["id"]: p for p in allocated.get("points", [])}
        points = [by_id[p["id"]] for p in points]
        # Report validation of the proposed candidate while preserving the user's current state.
        outcome = validate(allocated)
        free = _free_address_options(state, points)
        token = secrets.token_urlsafe(32)
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps({"equipment": equipment, "points": points}, ensure_ascii=False),
                                  diff_payload=json.dumps({"kind": "equipment", "base_hash": _state_hash(state)}, ensure_ascii=False),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=PREVIEW_TTL_HOURS)))
        session.commit()
        return {"token": token, "equipment": equipment, "points": points,
                "free_addresses": free, "validation": outcome}

    @app.post("/api/equipment/confirm")
    def confirm_equipment(body: EquipmentConfirmBody, session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        from backend.engine import validate, FUXA_TYPES
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview and preview.expires_at.tzinfo is None else (preview.expires_at if preview else now)
        if not preview or preview.owner_id != user.id or expiry <= now:
            if preview and expiry <= now:
                session.delete(preview); session.commit()
            raise HTTPException(404, "Equipment preview not found or expired")
        metadata = json.loads(preview.diff_payload or "{}")
        if metadata.get("kind") != "equipment":
            raise HTTPException(404, "Equipment preview not found or expired")
        state = state_for(session)
        if _state_hash(state) != metadata.get("base_hash"):
            raise HTTPException(409, "Project changed after preview; create a new preview")
        candidate = json.loads(preview.state_payload)
        equipment = candidate["equipment"]
        original_points = {p["id"]: p for p in candidate.get("points", [])}
        allowed = {"plc_variable", "scada_name", "table", "address", "codesys_type", "fuxa_type", "fuxa_type_override", "words", "divisor", "scale", "unit", "direction", "target", "hmi_address", "daq_enabled", "daq_interval", "comment", "io_address", "source_address", "source_table"}
        selected = []
        selected_ids = set()
        for edited in body.points:
            point_id = str(edited.get("id") or "")
            if point_id not in original_points or point_id in selected_ids:
                raise HTTPException(422, "Selected points must be unique points from this preview")
            selected_ids.add(point_id)
            point = dict(original_points[point_id])
            for key in allowed:
                if key in edited:
                    point[key] = edited[key]
            code_type = str(point.get("codesys_type") or "REAL").upper()
            point["words"] = 2 if code_type in {"REAL", "DINT", "UDINT", "DWORD"} else 1
            if not point.get("fuxa_type_override"):
                point["fuxa_type"] = state.get("settings", {}).get("type_map", FUXA_TYPES).get(code_type, FUXA_TYPES.get(code_type, point.get("fuxa_type", "")))
            selected.append(point)
        proposed = {**state, "equipment": [*state.get("equipment", []), equipment], "points": [*state.get("points", []), *selected]}
        outcome = validate(proposed)
        if outcome.get("errors"):
            raise HTTPException(422, detail=outcome)
        db.save_state(session, proposed)
        session.delete(preview)
        session.commit()
        return {"equipment": equipment, "points": selected, "validation": outcome}

    @app.post("/api/equipment")
    def add_equipment(body: EquipmentBody, session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        from backend.engine import allocate_addresses, validate
        state = state_for(session)
        equipment, points = _build_equipment_candidate(state, body)
        proposed = {**state, "equipment": [*state.get("equipment", []), equipment], "points": [*state.get("points", []), *points]}
        try:
            updated = allocate_addresses(proposed)
        except Exception as exc:
            raise HTTPException(422, f"Unable to allocate addresses: {exc}") from exc
        updated_by_id = {p["id"]: p for p in updated.get("points", [])}
        for point in state.get("points", []):
            updated_by_id[point["id"]]["address"] = point.get("address")
        points = [updated_by_id[p["id"]] for p in points]
        outcome = validate(updated)
        if outcome.get("errors"):
            raise HTTPException(422, detail=outcome)
        db.save_state(session, updated)
        session.commit()
        return {"equipment": equipment, "points": points}
    @app.delete("/api/equipment/{equipment_id}")
    def delete_equipment(equipment_id: str, session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        state = state_for(session)
        equipment = next((x for x in state.get("equipment", []) if x.get("id") == equipment_id), None)
        if not equipment:
            raise HTTPException(404, "Equipment not found")
        state["equipment"] = [x for x in state["equipment"] if x.get("id") != equipment_id]
        state["points"] = [x for x in state.get("points", []) if x.get("equipment_id") != equipment_id]
        db.save_state(session, state)
        session.commit()
        return {"status": "ok"}

    @app.post("/api/imports/preview")
    async def import_preview(request: Request, file: UploadFile = File(...), session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        name = Path(file.filename or "").name
        extension = Path(name).suffix.lower()
        if extension not in {".xlsx", ".xlsm", ".csv"}:
            raise HTTPException(415, "Upload an .xlsx, .xlsm, or .csv file")
        content = await file.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "Import file exceeds 20 MB")
        if not content:
            raise HTTPException(422, "Import file is empty")
        safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}{extension}"
        _store_file(resolved_data, "imports", safe_name, content)
        from backend.engine import import_excel, import_fuxa
        original = state_for(session)
        try:
            prospective = import_fuxa(content, original) if extension == ".csv" else import_excel(content, original)
        except Exception as exc:
            raise HTTPException(422, f"Could not parse import: {exc}") from exc
        diff = _diff(original, prospective)
        token = secrets.token_urlsafe(32)
        original_hash = hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps(prospective, ensure_ascii=False),
                                  diff_payload=json.dumps({"kind": "import", "diff": diff, "base_hash": original_hash}, ensure_ascii=False),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=PREVIEW_TTL_HOURS)))
        session.commit()
        return {"token": token, "diff": diff, "state": prospective}

    @app.post("/api/imports/apply")
    def apply_import(body: PreviewApply, session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        from backend.engine import validate
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        expiry = preview.expires_at.replace(tzinfo=timezone.utc) if preview and preview.expires_at.tzinfo is None else (preview.expires_at if preview else now)
        if not preview or preview.owner_id != user.id or expiry <= now:
            if preview and expiry <= now:
                session.delete(preview)
                session.commit()
            raise HTTPException(404, "Import preview not found or expired")
        state = json.loads(preview.state_payload)
        stored_preview = json.loads(preview.diff_payload)
        if stored_preview.get("kind") == "equipment":
            raise HTTPException(404, "Import preview not found or expired")
        current = state_for(session)
        current_hash = hashlib.sha256(json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if not secrets.compare_digest(current_hash, stored_preview.get("base_hash", "")):
            raise HTTPException(409, "Project state changed after this preview. Create a new import preview.")
        outcome = validate(state)
        if outcome.get("errors"):
            raise HTTPException(422, detail=outcome)
        db.save_state(session, state)
        session.delete(preview)
        session.commit()
        return {"state": state, "validation": outcome}

    def _generate(kind: str, session: Session, user: User):
        from backend.engine import export_excel, export_fuxa, validate
        state = state_for(session)
        result = validate(state)
        if result.get("errors"):
            raise HTTPException(409, detail=result)
        content = export_fuxa(state) if kind == "fuxa" else export_excel(state)
        suffix = ".csv" if kind == "fuxa" else ".xlsx"
        label = "fuxa" if kind == "fuxa" else "io-list"
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        stored_filename = f"{label}-{timestamp}-{uuid.uuid4().hex[:8]}{suffix}"
        settings = state.get("settings", {})
        version = str(settings.get("version", "1"))
        if not version.upper().startswith("V"):
            version = f"V{version}"
        if kind == "fuxa":
            download_name = f"SCADA_{version}-devices_1_export.csv"
        else:
            reference = str(settings.get("document_reference", "GAIA-ELEC-1451"))
            download_name = f"{reference}-{version} - IO List.xlsx"
        download_name = Path(download_name).name
        path = _store_file(resolved_data, "exports", stored_filename, content)
        log = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(),
               "username": user.username, "file_type": kind, "version": state.get("settings", {}).get("version", ""),
               "points": len(state.get("points", [])), "sha256": hashlib.sha256(content).hexdigest()}
        state.setdefault("generation_logs", []).append(log)
        db.save_state(session, state)
        session.commit()
        media = "text/csv; charset=utf-8" if kind == "fuxa" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return FileResponse(path, filename=download_name, media_type=media)

    @app.get("/api/exports/fuxa")
    def export_fuxa_route(session: Session = Depends(get_session), user: User = Depends(current_user)):
        return _generate("fuxa", session, user)

    @app.get("/api/exports/excel")
    def export_excel_route(session: Session = Depends(get_session), user: User = Depends(current_user)):
        return _generate("excel", session, user)

    @app.get("/api/backup")
    def backup(session: Session = Depends(get_session), user: User = Depends(current_user)):
        state = state_for(session)
        payload = json.dumps({"format": "gaia-io-backup", "version": 1, "state": state}, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"gaia-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        path = _store_file(resolved_data, "exports", filename, payload)
        return FileResponse(path, filename=filename, media_type="application/json")

    @app.post("/api/backup")
    async def restore_backup(file: UploadFile = File(...), session: Session = Depends(get_session), user: User = Depends(current_user), _: None = Depends(csrf)):
        content = await file.read(MAX_IMPORT_BYTES + 1)
        if len(content) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "Backup exceeds 20 MB")
        try:
            decoded = json.loads(content)
            state = decoded["state"] if decoded.get("format") == "gaia-io-backup" else decoded
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, "Invalid backup JSON") from exc
        if not isinstance(state, dict):
            raise HTTPException(422, "Backup state must be a JSON object")
        backup_name = f"restore-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}.json"
        _store_file(resolved_data, "imports", backup_name, content)
        from backend.engine import validate
        outcome = validate(state)
        if outcome.get("errors"):
            raise HTTPException(422, detail=outcome)
        db.save_state(session, state)
        session.commit()
        return {"state": state, "validation": outcome}

    from backend.v2_routes import register_v2_routes
    register_v2_routes(app, get_session, current_user, csrf)

    @app.api_route("/api/{unknown_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    def unknown_api_path(unknown_path: str):
        raise HTTPException(404, "API endpoint not found")

    static_root = Path(static_dir) if static_dir else Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if static_root.exists():
        app.mount("/", StaticFiles(directory=str(static_root), html=True), name="frontend")
    else:
        @app.get("/")
        def root():
            return JSONResponse({"name": "Gaia IO Generator", "api": "/api"})
    return app


app = create_app()
