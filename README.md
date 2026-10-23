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

`.env.example` documents configuration; the application reads environment variables. It does not load `.env` automatically. Startup applies migrations and initializes the v2 project from the supplied pack. Admin credentials must be configured on the first startup; existing accounts are preserved and passwords are hashed with Argon2. Never commit real credentials.

## Checks

```powershell
.venv/Scripts/python.exe -m pytest -q
cd frontend
npm run build
```

Tests cover legacy generation and the v2 pack, Excel round trips, exact FUXA output, reviewed imports, migration backups, and account preservation. In v2 the reference CSV test tags are retained as CSV-only references with warnings. Imported source files remain available in the persistent data directory.

## Coolify deployment

1. Create an application from this GitHub repository, branch `main`, on server `raspberrypi-gaia`.
2. Choose the **Dockerfile** build pack, root build directory and `/Dockerfile`. Coolify builds the frontend and Python container on the Pi.
3. Set the container port to **8000** (or set `PORT` and the matching Coolify port). Keep the existing FUXA resource and port untouched.
4. Add persistent storage mounted at **`/data`**, writable by container UID **10001**. This contains `app.db`, imported files and generated exports. Back up the volume separately.
5. Set `ADMIN_USERNAME`, a unique `ADMIN_PASSWORD` (12+ characters), a random `SECRET_KEY` (32+ characters), `DATA_DIR=/data`, `PORT=8000`, `COOKIE_SECURE=true`. Set `FORWARDED_ALLOW_IPS` to the trusted Coolify proxy address/network (or `*` only when the container is reachable exclusively through the isolated proxy network).
6. The Dockerfile includes a Python health probe for `GET /api/health`; Coolify detects it automatically. Configure the chosen domain, such as `plc-generator.gaiarefinery.ca`. HTTPS is terminated by the existing proxy/tunnel; the application serves HTTP internally.
7. Enable automatic deployment on pushes to `main` using the GitHub integration/webhook. Deploy and verify login, imports and both downloads through the public HTTPS domain.

Do not use local Docker builds, image registries, compose, GitHub Actions or SSH deployment scripts. No ARM-specific configuration is required.

## V2 stage 1: data and files

The current workspace starts from `reference-v2/02_excel` and the supplied FUXA
v4 CSV. Existing accounts remain valid. The v1 engineering data stays archived;
it is not merged into the new model. Types, instances, communications, physical
I/O, parameters, exchanges, and CODESYS identities have normalized SQL tables,
with complete immutable revision documents for lossless file exchange and audit.

Browse types and instances, inspect DUT members and source FB implementations,
read workbook sheets, download all eleven populated workbooks, and export the
FUXA CSV. To change data in this stage, edit an exported workbook and upload it.
Merge retains missing rows; Replace replaces only the uploaded workbook's scope.
Both modes require a preview and explicit application. TO CHECK values are kept
as text and reported without blocking otherwise valid imports. The source FUXA
CSV's two test tags are retained for exact compatibility and explicitly reported.

The latest full CODESYS snapshot is `reference-v2/latest_codesys.xml`. Its wrapped
Application can be inspected on import; future PLCopen generation must emit free
objects, never that Application wrapper. The approved changes to the pack's
proposals are recorded in `reference-v2/DECISIONS.md`.

Before a legacy database is migrated, the application creates
`/data/backups/legacy-v1-before-v2.sqlite3`, the legacy project JSON, and a `.tar.gz`
archive of the database plus persistent source/export files. Source and export
directories remain in the existing volume. V2 JSON backups contain project data,
not users/password hashes; retain volume backups for complete recovery.

Rollback requires restoring the archived v1 database **before** starting the old
v1 release: its Alembic installation does not know migration `0002_v2_model`.
Stop the generator, keep a separate copy of the current v2 volume, restore the
archived `app.db` while no application process has it open, then deploy commit
`b0481aa`. Restore other archived files only if needed. Do not alter FUXA's volume.

## V2 stage 2: reviewed PLCopen exports

The PLCopen panel generates a full program or a partial export focused on a type,
instance, or individual object. Review the proposed objects and their reasons,
adjust the selection if needed, then create a fresh preview. The XML download is
enabled after validation and acknowledgement of the review. The text summary can
also be downloaded when validation fails. A later engineering revision invalidates
both downloads; create a new preview to see the updated program.

Generated files contain free DUT/GVL/POU objects and their folders, never an
Application, device, task, library or the external `DUT_Union_Real_Word`.
An exported whole GVL replaces that whole GVL in CODESYS, including during partial
imports. Read the deletion and persistent-value warnings carefully.

Import the partial XML into a **copy** of the CODESYS project using Application →
Import PLCopenXML, inspect replacement objects and compile. Repeat with the full
XML in another copy. Confirm PA initializers and persistent attributes before any
real PLC download. The generator performs XSD and consistency checks, not compilation.

When a TCP array is too small, the report names the array, required element count
and device address count. Keep the engineering addresses, explicitly approve an
array-size change in the generator's declaration model, regenerate and validate,
then enlarge the matching Modbus TCP server zone in CODESYS and review its mapping.
Bits need `ceil(address_count / 8)` BYTE elements; registers need one WORD element
per register. For example, 1505 coils need 189 BYTEs (`0..188`), with a server zone
covering those coil addresses. This stage reports the required change; it does not
resize mapped arrays or devices automatically. Structured editing of mapped-array
declarations and refreshed CODESYS imports are delivered in later stages.

For an application rollback to stage 1, redeploy commit `1bb367d`; stage 2 adds no
database migration. Keep the current data volume. The earlier v1 rollback procedure
above still applies when reverting before the v2 schema.

## V2 stage 3: import the known CODESYS state

Upload an exported CODESYS PLCopen XML in the snapshot panel. Partial scope merges
the uploaded objects into the known baseline, retaining unrelated objects. Full
scope replaces the baseline object inventory and reports missing objects; it does
not delete engineering types, instances or workbook rows.

Preview the differences between the previous snapshot, incoming CODESYS objects
and generated program. ObjectIds, headers and folders are refreshed only after
application. For divergent contents, explicitly retain the generator version or
adopt a compatible existing FB/FNC body. Structured declarations and generated
GFC/FC code remain owned by the engineering model. Keeping the generator version
acknowledges that a later XML export can replace the CODESYS contents shown.

Apply rejects stale/expired previews and unresolved decisions. It records a new
project revision and invalidates previous XML download previews. Both import
scopes extract free program objects from an Application wrapper for comparison;
device/task/library configuration is never included in generated exports.

Stage 3 adds no database migration. Redeploy `e3230f0` for the previous stage,
keeping the data volume and an export of the current project backup.

Graphical authoring, ST editors and device configuration pages are later stages.
This release does not write to the PLC or change its devices, channels, tasks,
or running parameters.

## V2 stage 4: object views and authored code

The grouped navigation provides separate Types, Instances and Communications pages.
Select a type or instance to inspect its DUTs and FB, and the existing communications,
I/O, parameter and exchange links. An instance uses its type's code and declarations;
open the type to edit these shared definitions.

DUT declarations are generated read-only ST. Edit their structured member rows,
including order, defaults and comments. Review changes before applying them; referenced
members cannot be removed while their mappings or source code still use them. The
corresponding TYPE workbook is updated with the model for subsequent Excel exports.

The authored FB body and its additional custom code use separate ST editors with
syntax highlighting, line numbers and search. Generated interfaces remain read only.
Custom code is preserved verbatim and appended during generation. Preview validation
is a consistency check, not a CODESYS compiler; import the XML into a project copy
and compile before any PLC download. PA declaration changes can reset persistent
values; review the export warnings.

Communications currently provides a read-only view of TCP, RTU and physical I/O
configuration with instance links. Advanced communications settings remain stage 7;
type creation and graphical exchange authoring remain stages 5 and 6.

Stage 4 uses the existing revision schema, with no database migration. To roll back
the UI/API, redeploy `1ae97f4`, keep the current volume and retain a JSON backup of
its latest revision. Do not deploy an older v1 application against the v2 database.

## V1 archive and previous prototype limits

**Equipment type** defines reusable variables in the SCADA, IO, and COM categories. Instances expand their names into `GVL_SCADA.SCADA_{tag}`, `GVL_IO.IO_{tag}`, and `GVL_COM.COM_{tag}`. In **Equipment instance**, Add or Duplicate opens a preview with free exchange addresses. Select variables, edit each address manually, and confirm to save. Two-register values reserve both words; collisions are rejected. Deleting an instance removes its points and releases its allocations. If the saved project changes during a preview, create a new preview.

All categories reserve addresses in the four shared PLC exchange tables. Physical IO addresses and COM source registers are separate properties. FUXA CSV contains SCADA variables only; Excel and JSON backup retain all categories and their metadata. Type changes apply to newly created instances.

The catalogue, installed equipment and SCADA points are distinct collections. For this first single-project version they are saved as one transactional JSON aggregate in SQLite; users and pending import previews have separate SQLAlchemy tables. Alembic owns schema migrations. Multiple-user editing can overwrite concurrent changes; use one editor at a time until revision checking is added.

Excel exports use the reference SCADA tables and equipment sheets and include a hidden metadata sheet to retain application-only fields. Excel imports show differences before applying. FUXA IDs are stored with points; fixed CSV sections come from the last imported export. A backup includes project state and generation logs, not login credentials or source/upload files. Keep volume backups for full disaster recovery.

Out of scope: CODESYS code generation, Weintek addresses, multiple projects and PDF reports. Build and automated tests do not prove compatibility with the installed FUXA version; verify a generated CSV in that FUXA instance before using its tags in operation. The Coolify deployment will be verified when access is available.

## V2 stage 5: creation, mappings and instance editing

Types > Create type opens a six-step wizard. Start blank or copy a type, edit
DUT members, configure RTU/supervision, write PROCESSING and review before saving.
UPI local/auto reuse copies the selected UPI logic; review it for the intended
process. Complete equipment copies retain timer locals and interface types.

Existing FBs are converted automatically without changing their source. Add
explicit typed INPUTS/OUTPUTS links in the FB mapping editor; generated copies
are read only. PROCESSING and separate custom code remain editable. Legacy
conditions/calculations stay preserved. The grid supports drag pan, wheel/button
zoom, arrows, +/- and 0/Fit. Click a block to edit its object.

Instances is a table. Choose a type and name; review TCP/RTU addresses, edit rows
and update the preview before saving. PA, I/O and Modbus buttons open dialogs.
Blank PA fields inherit defaults. Deletion previews impacts and releases address
allocations, keeping physical channels. Incompatible inherited exchange calls
are skipped with a warning. Graphical exchange conditions remain stage 6.

No SQL schema change is required. Existing projects get one immutable revision
for FB metadata conversion; accounts and earlier revisions remain. If startup,
authentication, generation or instance editing regresses, redeploy stage 4 commit
91f6e2771527d2c25ab850d54eb1f1632a15e6cf with the same volume and retain a JSON
backup of the latest revision. No running PLC configuration is changed. Import
partial and complete XML into a CODESYS project copy and compile before download.


## V2 stage 6: graphical exchanges

Open Exchanges in the Conception menu. Arrange instances on the grid, then
connect a pair. The preview reuses a compatible FC_EXC for those types, including
a reversed pair, or creates one and an associated GFC_EXC call. Save layout
persists node positions and viewport without changing PLC behavior.

Open a shared function to wire DUT_EXC members. Select or drag compatible
source/destination ports; mappings can run in both directions. A change to that
function affects every pair using it. Add all/any condition terms and optionally
an Else variable or literal. With no Else, a false condition leaves the current
destination unchanged. WORD comparisons use explicit conversions.

Review generated ST before applying. Generated copies are read only; custom
ST stays separate and is preserved on regeneration. Excel exports retain
conditions and fallbacks as JSON in Condition/Else columns and retain Custom
code on FC_EXC. Malformed metadata, incompatible paths or invalid references
block the operation. The CODESYS compiler remains the final code check.

No database migration or automatic engineering-data replacement is required.
If authentication, exchange editing or export regresses, redeploy stage 5 commit
47f17225a590ca792f90b7bacfb7f8ea8c06daea with the same data volume. Keep a JSON
backup of the latest project revision. No PLC, FUXA or tunnel is changed.
