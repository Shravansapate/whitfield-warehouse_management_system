# Production readiness

The WMS core and browser application are complete for local/development use with PostgreSQL. The following items require deployment-specific infrastructure or vendor decisions and must be completed before handling production shipments.

## Required before production

- Select a carrier, obtain sandbox/production credentials, and implement the provider-neutral adapter with address validation, rates, label creation/voiding, tracking, timeouts, error normalization, and provider-side idempotency. The application rejects the built-in fake provider when `ENVIRONMENT=production`.
- Connect a separate authenticated voice service if voice workflows are required. It must forward the user's WMS JWT, read back every proposed receiving write, require explicit confirmation, supply an idempotency key, and set `X-Source: voice`. It must never access PostgreSQL directly.
- Terminate HTTPS at the load balancer or reverse proxy and set an explicit `CORS_ORIGINS` allow-list.
- Put login and sensitive-command rate limits at the shared ingress/API gateway. Per-process memory limits are insufficient for a multi-worker deployment.
- Use separate database roles: a controlled migration owner and a least-privilege runtime role. Preserve the immutable ledger triggers and deny runtime `UPDATE`/`DELETE` on `audit_logs` and `inventory_movements`.
- Store database, JWT, and carrier secrets in the deployment secret manager. Rotate the JWT secret under a planned session-expiry procedure.
- Configure structured log collection, request latency/failure metrics, database pool metrics, carrier/scheduler failure alerts, and dashboards without sensitive payloads.
- Decide whether low-stock checks run in one API process or an external scheduler. The built-in loop is read-only and failure-contained, but enabling it in multiple API workers intentionally produces one check stream per worker.
- Configure automated PostgreSQL backups, retention, encryption, and restore drills. Verify a restore before go-live and periodically afterward.
- Run load/performance tests using realistic receipt and order contention, then size API workers and the database pool together.
- Add a production smoke test for login, warehouse isolation, receipt finalization, allocation, label sandboxing, shipping, cancellation, and audit reconciliation.

## Deliberate development behavior

- `/dev-labels/{fingerprint}.svg` is only available outside production and is visibly marked **not for shipment**.
- The fake carrier is deterministic for idempotency tests; it does not call a shipping network or purchase postage.
- The voice button reports setup status until the separate service is connected.
- The in-process low-stock scheduler is disabled by default. When enabled, its validated interval is 60 to 86,400 seconds; immediate mode runs once at startup and all later attempts use a fixed delay after the previous attempt completes.
- Docker Compose uses one database owner for local convenience. Do not copy that privilege model to production.

## Go-live data procedure

1. Apply Alembic migrations with the migration role.
2. Supply `SEED_OWNER_NAME`, `SEED_OWNER_EMAIL`, and a unique 14+ character `SEED_OWNER_PASSWORD` through the deployment secret environment, then run `python -m backend.seed --environment production --bootstrap-owner`. This command creates Reno, Columbus, and the first owner in one transaction.
3. Verify owner login, remove the bootstrap password from the job environment, and create staff identities through the owner workflow. The bootstrap command refuses demo mode, environment mismatch, conflicting/pre-existing identities, disabled/demoted owners, and stale passwords; only an exact active-owner replay is idempotent.
4. Create products through the owner workflow/API.
5. Post verified opening quantities through the owner-only opening-balance command with a reason and audit record.
6. Reconcile each balance against movements, disable any one-time bootstrap access, and take the first backup.
