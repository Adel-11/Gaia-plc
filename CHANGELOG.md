# Changelog

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
