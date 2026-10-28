"""Official template downloads preserve every workbook feature and project revision."""
import io
import zipfile

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.v2_templates import TEMPLATES
from backend.v2_engine import DEFAULT_PACK


def test_template_downloads_are_authenticated_unmodified_and_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_USERNAME", "templates-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "template-test-password")
    app = create_app(data_dir=tmp_path, secret_key="templates-test-key-" * 3, cookie_secure=False)
    with TestClient(app) as client:
        for path in ("/api/v2/templates", "/api/v2/templates.zip", "/api/v2/templates/TEMPLATE_TYPE.xlsx"):
            assert client.get(path).status_code == 401
        assert client.post("/api/login", json={"username": "templates-admin", "password": "template-test-password"}).status_code == 200
        before = client.get("/api/v2/project").json()
        assert {row["filename"] for row in client.get("/api/v2/templates").json()["templates"]} == set(TEMPLATES)
        bundle = client.get("/api/v2/templates.zip")
        assert bundle.status_code == 200
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            assert set(archive.namelist()) == set(TEMPLATES)
            for name in TEMPLATES:
                expected = (DEFAULT_PACK / "02_excel" / "templates" / name).read_bytes()
                individual = client.get(f"/api/v2/templates/{name}")
                assert individual.status_code == 200
                assert name in individual.headers["content-disposition"]
                assert individual.content == archive.read(name) == expected
        assert client.get("/api/v2/templates/TYPE_DIP1500.xlsx").status_code == 404
        assert client.get("/api/v2/templates/..%5C..%5Calembic.ini").status_code == 404
        after = client.get("/api/v2/project").json()
        assert after["revision"] == before["revision"]
        assert after["version"] == before["version"]
