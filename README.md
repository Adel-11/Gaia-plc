# Gaia IO Generator

A single-project workspace for Gaia Refinery equipment, PLC/SCADA exchange points, FUXA CSV and IO List Excel generation. English interface, FastAPI + React, SQLite, SQLAlchemy and Alembic.

## Local development (without Docker)

Use Python 3.12 and Node.js 22 or newer.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
$env:DATA_DIR = "$PWD/local-data"
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "choose-a-long-unique-password"
$env:SECRET_KEY = "replace-with-a-random-secret-at-least-32-characters"
$env:COOKIE_SECURE = "false"
.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to port 8000. For a single-server preview, run `npm run build` in `frontend`, then restart the backend and open http://localhost:8000.

`.env.example` documents configuration; the application reads environment variables. It does not load `.env` automatically. Startup applies migrations and seeds the provided reference project and three equipment types. Admin credentials must be configured on the first startup; passwords are hashed with Argon2. Never commit real credentials.

## Checks

```powershell
.venv/Scripts/python.exe -m pytest -q
cd frontend
npm run build
```

Generation tests use the supplied real IO List and FUXA exports. Test tags `TEST Holding` and `Test descrete` are excluded because their addresses conflict with real points. Imported source files remain available in the persistent data directory.

## Coolify deployment

1. Create an application from this GitHub repository, branch `main`, on server `raspberrypi-gaia`.
2. Choose the **Dockerfile** build pack, root build directory and `/Dockerfile`. Coolify builds the frontend and Python container on the Pi.
3. Set the container port to **8000** (or set `PORT` and the matching Coolify port). Keep the existing FUXA resource and port untouched.
4. Add persistent storage mounted at **`/data`**, writable by container UID **10001**. This contains `app.db`, imported files and generated exports. Back up the volume separately.
5. Set `ADMIN_USERNAME`, a unique `ADMIN_PASSWORD` (12+ characters), a random `SECRET_KEY` (32+ characters), `DATA_DIR=/data`, `PORT=8000`, `COOKIE_SECURE=true`. Set `FORWARDED_ALLOW_IPS` to the trusted Coolify proxy address/network (or `*` only when the container is reachable exclusively through the isolated proxy network).
6. The Dockerfile includes a Python health probe for `GET /api/health`; Coolify detects it automatically. Configure the chosen domain, such as `plc-generator.gaiarefinery.ca`. HTTPS is terminated by the existing proxy/tunnel; the application serves HTTP internally.
7. Enable automatic deployment on pushes to `main` using the GitHub integration/webhook. Deploy and verify login, imports and both downloads through the public HTTPS domain.

Do not use local Docker builds, image registries, compose, GitHub Actions or SSH deployment scripts. No ARM-specific configuration is required.

## Prototype architecture and limits

**Equipment type** defines reusable variables in the SCADA, IO, and COM categories. Instances expand their names into `GVL_SCADA.SCADA_{tag}`, `GVL_IO.IO_{tag}`, and `GVL_COM.COM_{tag}`. In **Equipment instance**, Add or Duplicate opens a preview with free exchange addresses. Select variables, edit each address manually, and confirm to save. Two-register values reserve both words; collisions are rejected. Deleting an instance removes its points and releases its allocations. If the saved project changes during a preview, create a new preview.

All categories reserve addresses in the four shared PLC exchange tables. Physical IO addresses and COM source registers are separate properties. FUXA CSV contains SCADA variables only; Excel and JSON backup retain all categories and their metadata. Type changes apply to newly created instances.

The catalogue, installed equipment and SCADA points are distinct collections. For this first single-project version they are saved as one transactional JSON aggregate in SQLite; users and pending import previews have separate SQLAlchemy tables. Alembic owns schema migrations. Multiple-user editing can overwrite concurrent changes; use one editor at a time until revision checking is added.

Excel exports use the reference SCADA tables and equipment sheets and include a hidden metadata sheet to retain application-only fields. Excel imports show differences before applying. FUXA IDs are stored with points; fixed CSV sections come from the last imported export. A backup includes project state and generation logs, not login credentials or source/upload files. Keep volume backups for full disaster recovery.

Out of scope: CODESYS code generation, Weintek addresses, multiple projects and PDF reports. Build and automated tests do not prove compatibility with the installed FUXA version; verify a generated CSV in that FUXA instance before using its tags in operation. The Coolify deployment will be verified when access is available.
