"""Authenticated stage-one project files and reviewed imports for Generator v2."""
from __future__ import annotations

import io
import json
import secrets
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.db import ImportPreview


class ApplyV2Import(BaseModel):
    token: str


def register_v2_routes(app, get_session, current_user, csrf):
    from backend import v2_engine as engine, v2_store as store
    from backend.v2_codesys_routes import register_codesys_routes
    from backend.v2_plc_routes import register_plc_routes

    register_plc_routes(app, get_session, current_user, csrf)
    register_codesys_routes(app, get_session, current_user, csrf)

    def project_for(session):
        state = store.load_project(session)
        if state is None:
            raise HTTPException(503, "The v2 project has not been initialized")
        return state

    def download(content: bytes, filename: str, media_type: str):
        target = Path(app.state.data_dir) / "exports" / f"v2-{uuid.uuid4().hex}-{filename}"
        target.write_bytes(content)
        return FileResponse(target, filename=filename, media_type=media_type)

    @app.get("/api/v2/project")
    def get_project(session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        types = state.get("types", [])
        workbooks = []
        for filename, workbook in state.get("workbooks", {}).items():
            workbooks.append({"filename": filename, "sheets": [
                {"name": sheet["name"], "state": sheet.get("state", "visible"),
                 "row_count": len(sheet.get("rows", [])), "headers": sheet.get("headers", [])}
                for sheet in workbook.get("sheets", [])]})
        result = {key: value for key, value in state.items()
                  if key not in {"workbooks", "fuxa_csv", "fuxa_template", "source_files"}}
        result.update(phase=3, workbooks=workbooks, validation=engine.validate_project(state), counts={
            "types": len(types), "instances": len(state.get("instances", [])),
            "duts": sum(len(t.get("duts", [])) for t in types),
            "dut_members": sum(len(d.get("members", [])) for t in types for d in t.get("duts", [])),
            "fuxa_signals": len(state.get("fuxa", {}).get("points", [])),
            "tcp_points": len(state.get("tcp_points", [])),
            "exchange_functions": len(state.get("exchange_functions", [])),
        })
        if isinstance(result.get("snapshot"), dict):
            result["snapshot"] = {**result["snapshot"], "object_count": sum(
                obj.get("kind") in {"DUT", "GVL", "POU", "function", "functionBlock", "program"}
                for obj in result["snapshot"].get("objects", []))}
        return result

    @app.get("/api/v2/workbooks/{filename}")
    def get_workbook(filename: str, session=Depends(get_session), user=Depends(current_user)):
        workbook = project_for(session).get("workbooks", {}).get(filename)
        if workbook is None:
            raise HTTPException(404, "Workbook not found")
        return {"filename": filename, **workbook}

    @app.get("/api/v2/exports/fuxa.csv")
    def export_fuxa(session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        errors = engine.validate_project(state).get("errors", [])
        if errors:
            raise HTTPException(409, detail={"errors": errors})
        try:
            content = engine.export_fuxa(state)
        except ValueError as exc:
            raise HTTPException(409, f"FUXA export rejected: {exc}") from exc
        return download(content, "SCADA_V1-devices_v4.csv", "text/csv; charset=utf-8")

    @app.get("/api/v2/exports/{filename}")
    def export_workbook(filename: str, session=Depends(get_session), user=Depends(current_user)):
        state = project_for(session)
        if filename not in state.get("workbooks", {}):
            raise HTTPException(404, "Workbook not found")
        try:
            content = engine.export_workbook(state, filename)
        except ValueError as exc:
            raise HTTPException(409, f"Excel export rejected: {exc}") from exc
        return download(content, filename,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/api/v2/backup")
    def backup(session=Depends(get_session), user=Depends(current_user)):
        payload = {"format": "gaia-generator-v2-backup", "version": 2,
                   "state": project_for(session)}
        return download(json.dumps(payload, ensure_ascii=False, indent=2).encode(),
                        "gaia-generator-v2-backup.json", "application/json")

    @app.post("/api/v2/imports/preview")
    async def preview_import(file: UploadFile = File(...), mode: str = Form("merge"),
                             session=Depends(get_session), user=Depends(current_user),
                             _=Depends(csrf)):
        if mode not in {"merge", "replace"}:
            raise HTTPException(422, "Import mode must be merge or replace")
        filename = Path(file.filename or "").name
        if not filename.lower().endswith(".xlsx"):
            raise HTTPException(415, "Upload a v2 .xlsx workbook")
        data = await file.read(20 * 1024 * 1024 + 1)
        if not data or len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, "Workbook must contain between 1 byte and 20 MB")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if len(archive.infolist()) > 1000 or sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError("Workbook expands beyond the supported size")
                if any(x.flag_bits & 1 for x in archive.infolist()):
                    raise ValueError("Encrypted workbooks are not supported")
        except (ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, f"Invalid workbook: {exc}") from exc
        current = project_for(session)
        try:
            result = engine.import_workbook(data, filename, current, mode=mode)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"Could not import workbook: {exc}") from exc
        stored = Path(app.state.data_dir) / "imports" / f"v2-{uuid.uuid4().hex}.xlsx"
        stored.write_bytes(data)
        token = secrets.token_urlsafe(32)
        metadata = {"kind": "v2-workbook", "base_revision": current.get("revision", 0),
                    "filename": filename, "mode": mode, "source_file": stored.name,
                    "diff": result.get("diff", {})}
        session.add(ImportPreview(token=token, owner_id=user.id,
                                  state_payload=json.dumps(result["state"], ensure_ascii=False),
                                  diff_payload=json.dumps(metadata, ensure_ascii=False),
                                  expires_at=datetime.now(timezone.utc) + timedelta(hours=2)))
        session.commit()
        return {"token": token, "filename": filename, "mode": mode,
                "diff": result.get("diff", {}), "validation": result["validation"]}

    @app.post("/api/v2/imports/apply")
    def apply_import(body: ApplyV2Import, session=Depends(get_session),
                     user=Depends(current_user), _=Depends(csrf)):
        preview = session.get(ImportPreview, body.token)
        now = datetime.now(timezone.utc)
        if not preview or preview.owner_id != user.id:
            raise HTTPException(404, "Import preview not found or expired")
        expiry = preview.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            raise HTTPException(404, "Import preview not found or expired")
        metadata = json.loads(preview.diff_payload)
        if metadata.get("kind") != "v2-workbook":
            raise HTTPException(404, "Import preview not found or expired")
        state = json.loads(preview.state_payload)
        current = project_for(session)
        if current.get("revision", 0) != metadata.get("base_revision"):
            raise HTTPException(409, "Project changed after preview; create a new preview")
        validation = engine.validate_project(state)
        if validation.get("errors"):
            raise HTTPException(422, detail=validation)
        try:
            saved = store.save_project(session, state, username=user.username,
                                       expected_revision=metadata["base_revision"])
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        session.delete(preview)
        session.commit()
        return {"revision": saved["revision"], "validation": validation}
