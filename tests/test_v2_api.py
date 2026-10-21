"""Stage-one acceptance checks through the authenticated HTTP API."""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import openpyxl
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from backend.main import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_v2_keeps_legacy_account_and_database_and_reviews_imports(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    database = tmp_path / "app.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "0001_initial")
    legacy = {"settings": {"project_name": "Do not lose this"},
              "equipment_types": [], "equipment": [], "points": []}
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,datetime('now'))",
                           ("existing-admin", PasswordHash.recommended().hash("existing-admin-password")))
        connection.execute("INSERT INTO project_state (id,payload,updated_at) VALUES (1,?,datetime('now'))",
                           (json.dumps(legacy),))
    application = create_app(data_dir=tmp_path, secret_key="v" * 48, cookie_secure=False)
    with TestClient(application) as client:
        assert client.get("/api/v2/project").status_code == 401
        logged_in = client.post("/api/login", json={"username": "existing-admin", "password": "existing-admin-password"})
        assert logged_in.status_code == 200
        headers = {"X-CSRF-Token": logged_in.json()["csrf_token"]}
        first = client.get("/api/v2/project").json()
        assert first["counts"]["types"] == 6
        assert first["counts"]["instances"] == 6
        assert first["counts"]["duts"] == 24
        assert first["validation"]["errors"] == []
        assert json.loads((tmp_path / "backups/legacy-v1-before-v2.json").read_text()) == legacy
        with sqlite3.connect(tmp_path / "backups/legacy-v1-before-v2.sqlite3") as connection:
            assert connection.execute("SELECT username FROM users").fetchone()[0] == "existing-admin"
        import tarfile
        with tarfile.open(tmp_path / "backups/legacy-v1-before-v2.tar.gz") as archive:
            assert "app.db" in archive.getnames()
        reference = (ROOT / "reference-v2/04_fuxa/SCADA_V1-devices_v4.csv").read_bytes()
        assert client.get("/api/v2/exports/fuxa.csv").content == reference

        downloaded = client.get("/api/v2/exports/TYPE_DIP1500.xlsx")
        assert downloaded.status_code == 200
        workbook = openpyxl.load_workbook(io.BytesIO(downloaded.content))
        workbook["TYPE"]["B4"] = "Reviewed pump description"
        content = io.BytesIO()
        workbook.save(content)
        arguments = {"files": {"file": ("TYPE_DIP1500.xlsx", content.getvalue())}, "data": {"mode": "merge"}}
        assert client.post("/api/v2/imports/preview", **arguments).status_code == 403
        preview = client.post("/api/v2/imports/preview", headers=headers, **arguments)
        assert preview.status_code == 200, preview.text
        draft = preview.json()
        assert draft["validation"]["errors"] == []
        assert client.get("/api/v2/project").json()["revision"] == first["revision"]
        stale = client.post("/api/v2/imports/preview", headers=headers, **arguments).json()
        applied = client.post("/api/v2/imports/apply", headers=headers, json={"token": draft["token"]})
        assert applied.status_code == 200, applied.text
        assert client.post("/api/v2/imports/apply", headers=headers, json={"token": draft["token"]}).status_code == 404
        assert client.post("/api/v2/imports/apply", headers=headers, json={"token": stale["token"]}).status_code == 409
        backup = client.get("/api/v2/backup")
        assert "password_hash" not in backup.text
    restarted = create_app(data_dir=tmp_path, secret_key="v" * 48, cookie_secure=False)
    with TestClient(restarted) as client:
        assert client.post("/api/login", json={"username": "existing-admin", "password": "existing-admin-password"}).status_code == 200
        types = client.get("/api/v2/project").json()["types"]
        assert next(t for t in types if t["name"] == "DIP1500")["description"] == "Reviewed pump description"
