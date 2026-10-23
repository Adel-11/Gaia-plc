# Changelog

## 0.8.0 - 2026-10-23 - V2 stage 7

- Add a dedicated PA page with instance overrides, inherited defaults and comparison by equipment type. Supervision editability remains information only.
- Add reviewed configuration edits for TCP clients/ranges/points, RTU line/slave/channel settings and physical I/O mappings over the 11-slot rack.
- Allocate free TCP addresses from configured ranges with shared occupancy; keep existing mapped variable names.
- Preserve engineering workbooks and custom ST through configuration updates; reject stale or expired review tokens.
- Show manual CODESYS/device actions; no PLC, FUXA or network configuration is changed automatically.
- Stage 6 XML import and compilation accepted by the user before this stage.

## 0.7.0 - 2026-10-23 - V2 stage 6

- Add the Exchanges page: instance palette, persistent canvas positions, visible
  call connections, zoom/pan, and shared FC member wiring in both directions.
- Reuse compatible existing exchange functions or create a new FC and its
  GFC_EXC call through a reviewed preview.
- Author typed conditional copies with all/any terms and an optional Else
  variable or literal. With no Else, a false condition retains the destination.
- Keep generated ST read only and custom code separate; preserve it on mapping
  changes. Conditions and fallbacks round-trip through Excel metadata columns.
- Validate member types, call parameter types, conditions and PLCopen references;
  partial exports include the changed exchange and its GFC_EXC caller.
- Existing reference code and calls remain unchanged until edited. Actual import
  and compilation of the supplied XML in a CODESYS copy remain manual acceptance.
- Prevent browser caching of the HTML shell so deployments load current assets.

## 0.6.0 - 2026-10-22 - V2 stage 5

- Add a reviewed six-step type creation wizard: blank/copy, DUTs, RTU,
  supervision and PROCESSING, with PLCopen validation before saving.
- Automatically convert all FBs to INPUTS / PROCESSING / OUTPUTS metadata
  without changing source. Generate explicit typed mappings while preserving
  legacy conditions, calculations and separate custom code.
- Add connected objects over a grid with zoom, pan, keyboard controls and Fit;
  provide IEC primitive, union and custom member types.
- Replace the instance view with a creation table and PA, I/O and Modbus dialogs.
  Review/edit addresses before saving, reject collisions and release allocations
  on deletion while retaining immutable revision history.
- Preserve copied FB local declarations and Excel roundtrips. Warn rather than
  connect a copied type to FC parameters with incompatible DUT types.
- CODESYS import/compile in a project copy remains manual acceptance before
  any PLC download.

## 0.5.0 - 2026-10-22 - V2 stage 4

- Add graphical object views for types and instances, with DUTs around the FB and
  linked TCP, RTU, physical I/O, parameter and exchange records.
- Edit structured DUT members and their order; keep generated declarations read only.
- Add Structured Text editors with highlighting, line numbers and search for the
  authored FB body and its separate custom-code section.
- Preview and validate edits before saving an immutable revision; retain workbook
  data and reject stale edits and referenced-member removals.
- Split Types, Instances and Communications into dedicated pages in collapsible
  navigation groups. Communications displays existing configuration; advanced
  device/address editing remains scheduled for stage 7.

## 0.4.1 - 2026-10-22 - Workspace navigation

- Split the workspace into six vertical tabs with dedicated page themes: overview,
  equipment, workbooks/imports, PLC generation, CODESYS reference and exports.
- Preserve drafts and selections across tabs, support direct hash links and keyboard
  navigation, and keep data tables at a readable 16px size.
- Replace the redundant project sidebar with the functional page navigation.

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
