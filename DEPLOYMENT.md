# Production deployment

Configured on October 14, 2026.

- Application: Gaia IO Generator, project Gaia Refinery / production in Coolify.
- Source: Adel-11/Gaia-plc, branch main, existing GitHub App integration.
- Deployment/build server: raspberrypi-gaia (Linux aarch64, Raspberry Pi 4).
- Build pack: Dockerfile, base directory /, Dockerfile /Dockerfile.
- Internal application port: 8000. No application host port mapping.
- Coolify domain: http://plc-generator.gaiarefinery.ca, internal port 8000.
- Persistent named volume: l3igl6w5n0skcd3wkdiddois-gaia-io-generator-data mounted at /data.
- Image healthcheck: python -m backend.healthcheck, requesting /api/health.
- Runtime: DATA_DIR=/data, PORT=8000, COOKIE_SECURE=true.
- Proxy trust: FORWARDED_ALLOW_IPS=127.0.0.1,172.19.0.2. The latter was resolved from coolify-proxy inside the application container; review it if the proxy network is recreated.
- ADMIN_PASSWORD and SECRET_KEY exist only in Coolify, are runtime-only, and are not committed. Automatic build argument injection is disabled.
- Administrator username: admin. Retrieve the initial password from the ADMIN_PASSWORD environment variable in Coolify. Editing this bootstrap variable does not change an existing user's database password.
- Automatic deployments on main pushes are enabled through the existing GitHub App/webhooks.

## Cloudflare

The existing raspberrypi-gaia tunnel is reused (38ec45d4-acaa-4b2f-a80e-586c0b5ec9b3).

- plc-generator.gaiarefinery.ca routes to http://127.0.0.1:80 (Coolify proxy). Cloudflare terminates public HTTPS.
- plc.gaiarefinery.ca retains its existing route to http://localhost:1881 (FUXA).
- The obsolete scada.gaiarefinery.ca route to http://127.0.0.1:10008 and its associated DNS were removed with explicit approval. Its application/data were not deleted.

## Verification

The first build and deployment completed successfully on the Pi. The container passed its health probe. Public HTTPS sign-in succeeded and loaded 79 reference SCADA points and three equipment instances. The downloaded production FUXA CSV matched the expected reference after normalizing tag IDs and excluding the two test tags; LF, trailing newlines and 20 tag commas were verified. The downloaded Excel workbook opened with openpyxl, with 11 sheets including metadata and nine structured tables. A project backup contained 79 points and two generation history records.

FUXA's existing public login screen was reachable. Generated tags were not imported into the running FUXA instance.

## Recovery

Use Coolify's deployment history and rollback for an application regression. Preserve the /data volume during a rollback or resource replacement. Export a JSON project backup before major data changes; volume backups also preserve users and uploaded/generated files. Do not delete or replace the shared Cloudflare tunnel or its plc.gaiarefinery.ca route.
