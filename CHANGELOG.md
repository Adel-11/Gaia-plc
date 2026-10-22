# Changelog

## 0.4.0 - 2026-10-22 - V2 stage 3

- Add reviewed CODESYS XML snapshot imports, with partial-merge and full-snapshot
  scopes, refreshed ObjectIds, headers and folder paths.
- Compare incoming objects against both the previous CODESYS snapshot and the
  generator model. Show source/generated ST and require explicit conflict choices.
- Preserve engineering declarations and workbook data. Adoption of CODESYS bodies
  is limited to existing FB/FNC objects with compatible interfaces.
- Protect import previews with CSRF, ownership, expiry and revision checks; keep
  immutable project revisions and invalidate older PLCopen export previews.
- Block exports that still contain unresolved CODESYS import differences.
- Continue to exclude Application, devices, tasks and libraries from generated XML.

## 0.3.0 - 2026-10-22 - V2 stage 2

- Generate PLCopen free objects from the structured model: DUTs, instance GVLs,
  communication and I/O copies, exchange functions/calls, FB calls and cycle order.
- Preserve FB/FNC implementations, mapped identifiers, existing ObjectIds and
  CODESYS headers. New object identities are stable and recorded with export previews.
- Add full and focused partial exports with an adjustable object selection,
  inclusion reasons, manual deletions/mapping actions, and persistent-data warnings.
- Validate XML against the supplied PLCopen XSD and check references and array/bit
  bounds before enabling download. Never include Application/device configuration,
  tasks, libraries or the externally supplied DUT_Union_Real_Word.
- Store reviewed export bytes behind owned, expiring tokens. Engineering edits
  invalidate previous previews. Text reports remain downloadable on validation errors.
- Add independent deep regression tests against V7 from the V6 snapshot and both
  partial-example object sets, plus mutation and authenticated export tests.
- Keep the CODESYS snapshot importer, graphical editing and ST editing for later
  stages. Real import and compilation in a copy of the PLC project require Adel's
  validation; automated checks do not claim to replace the CODESYS compiler.

## 0.2.0 - 2026-10-21 - V2 stage 1

- Start the engineering model from the supplied v2 pack: six types, six
  instances, 24 DUTs, nine exchange functions, and eleven populated workbooks.
- Add normalized SQLAlchemy model tables and immutable project revisions with
  an Alembic migration; preserve existing user accounts and legacy project data.
- Before migration, archive the v1 SQLite database, project JSON, and persistent
  source/export files under `/data/backups/legacy-v1-before-v2.*`.
- Add an English data workspace with type/instance search, kind filtering,
  read-only DUT members and source FB code, and workbook/sheet inspection.
- Import v2 Excel files with merge or workbook-scoped replace previews,
  validation, CSRF/ownership checks, and stale-preview protection.
- Export Excel from the official templates; preserve workbook data, tooltips,
  validation lists, and unknown columns. Preserve FUXA tag IDs and exact v4 CSV
  bytes, including the two legacy test tags, which are reported as warnings.
- Keep the latest full CODESYS export as a read-only reference snapshot. Its
  Application wrapper is not a template for future generated XML exports.
- Record Adel's approved custom-ST, graphical-conditions, PA, RTU-naming, and
  declaration-editing decisions in `reference-v2/DECISIONS.md`.
- Automated acceptance covers workbook round-trips, CSV regeneration, relational
  foreign keys, legacy account/database preservation, reviewed imports, and restart.
- Not delivered in stage 1: PLCopen generation/validation, ST editing or custom
  section composition, graphical object/exchange editors, and device setup pages.
  No generated XML has been imported into CODESYS as part of this stage.

## 0.1.0 - 2026-10-14

- First English-language Gaia IO Generator prototype with catalogue, equipment and editable SCADA exchange tables.
- Reference Excel/FUXA imports with diff previews, strict exports and stable tag IDs.
- Admin authentication, Argon2 passwords, signed cookies and CSRF checks.
- SQLite persistence, Alembic migrations, project JSON backups and generation history.
- Single Dockerfile and documented Coolify deployment with persistent `/data` storage.
- Automated reference generation, workbook round-trip and API checks.
- Disabled equipment is excluded from both generated files and retained in workbook backup metadata.
- Added a standard-library Python container health probe for Coolify deployments.
- Configured production Coolify deployment, Cloudflare hostname and persistent volume; verified login and downloaded exports on the Raspberry Pi.
