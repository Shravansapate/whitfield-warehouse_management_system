# Whitfield Fulfillment WMS

A PostgreSQL-backed warehouse management application for the independent Reno and Columbus operations. It replaces the original mock UI with a live FastAPI API and implements scanner-first receiving, warehouse-scoped inventory, atomic multi-line order allocation, fulfillment, damaged returns, user access, and immutable operational history.

## What works

- Argon2 login, short-lived JWT access tokens, rotating/revocable refresh sessions, and owner-managed users.
- Strict warehouse scoping for manager, trusted, and staff accounts. Only an owner can select either warehouse.
- Draft inbound receipts with tracking/ticket duplicate protection, UPC/SKU lookup, accepted/damaged validation, finalization, cancellation, and damaged-return completion.
- `on_hand`, `reserved`, and `available` balances with reasoned adjustments, opening balances, thresholds, and an append-only movement ledger.
- Multi-product, single-warehouse, all-or-nothing order allocation using conditional PostgreSQL updates. There is no automatic cross-warehouse fallback.
- Distinct allocated, picking, packed, label-created, shipped, cannot-fulfill, and cancelled states, with a persisted per-line picking checklist that must be complete before packing.
- Required package measurements, a deterministic development carrier, printable development labels, and shipment posting.
- Owner/manager dashboards and audit history, with database triggers preventing audit or movement updates/deletes.
- Responsive React UI with loading/error/empty states and permission-aware navigation.

The development carrier is intentionally fake. A real carrier adapter and the separate voice-assistant service require the provider choices and credentials that were not supplied; see [Production readiness](docs/production-readiness.md).

## Architecture

```text
React/Vite :5173
      |
      | /api/v1 (Vite proxy in development)
      v
FastAPI :8000
      |
      | SQLAlchemy async + asyncpg
      v
PostgreSQL 18
```

Mutating commands use the route -> controller/service -> CRUD structure defined by the local Eigi conventions. Business records, inventory balances, movements, audit rows, and idempotent responses commit in the same PostgreSQL transaction.

Operational list endpoints keep JSON array response bodies and use opaque keyset
pagination. Set `limit`, select a documented `sort`, and pass the
`X-Next-Cursor` response header back as the next request's `cursor` without
decoding or changing it. Keep the same filters and sort while following a cursor.
The React screens do this through their **Load more** controls. Orders, receipts,
damaged returns, inventory movements, and audit history support creation-date and
domain status/type filters; inventory supports product search plus name or
availability sorting; users support name/email, role, active-state, assignment,
and creation-date filters; product search supports active-state and creation-date
filters. Every non-owner inventory query remains bound to the actor's assigned
warehouse before a cursor is parsed.

## Requirements

- Python 3.12+
- Node.js 20+
- PostgreSQL 16+ (PostgreSQL 18 is used in Docker Compose)
- PowerShell examples below assume Windows; equivalent shell commands work on macOS/Linux.

Docker is optional. It was not installed in the implementation environment, so the native PostgreSQL path is the locally verified path.

## Native PostgreSQL setup

From the repository root, create an application database and a separate disposable test database. Replace the example passwords before use:

```powershell
$pgBin = 'C:\Program Files\PostgreSQL\18\bin'
& "$pgBin\psql.exe" -h 127.0.0.1 -U postgres -d postgres
```

Run this SQL in `psql`:

```sql
CREATE ROLE wms_app LOGIN PASSWORD 'replace-with-app-password';
CREATE DATABASE whitfield_wms OWNER wms_app;
CREATE ROLE wms_test LOGIN PASSWORD 'replace-with-test-password';
CREATE DATABASE whitfield_wms_test OWNER wms_test;
CREATE DATABASE whitfield_wms_e2e_test OWNER wms_test;
```

If the roles already exist, alter their passwords or reuse the existing credentials rather than recreating them.

Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

Copy [backend/.env.example](backend/.env.example) to `backend/.env` and replace every placeholder. Passwords containing URL-reserved characters must be percent-encoded inside `DATABASE_URL`.

```env
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://wms_app:replace-with-app-password@127.0.0.1:5432/whitfield_wms
TEST_DATABASE_URL=postgresql+asyncpg://wms_test:replace-with-test-password@127.0.0.1:5432/whitfield_wms_test
JWT_SECRET_KEY=replace-with-at-least-32-random-characters
CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
LOW_STOCK_SCHEDULER_ENABLED=false
LOW_STOCK_SCHEDULER_INTERVAL_SECONDS=900
LOW_STOCK_SCHEDULER_RUN_IMMEDIATELY=true
SEED_OWNER_NAME=Whitfield Owner
SEED_OWNER_EMAIL=owner@example.com
SEED_OWNER_PASSWORD=replace-with-a-development-password
```

Apply migrations and add the two warehouses plus development demonstration data:

```powershell
python -m alembic -c backend\alembic.ini upgrade head
python -m backend.seed --environment development --demo
```

The demo seed creates `owner@example.com`, `manager@example.com`, `trusted@example.com`, and `staff@example.com`. They use the value in `SEED_OWNER_PASSWORD`; the three non-owner users are assigned to Reno. The seed is idempotent and refuses `--demo` in production.

For a non-demo deployment, the first owner is an explicit, one-time bootstrap rather than an automatic startup side effect. Configure `SEED_OWNER_NAME`, `SEED_OWNER_EMAIL`, and a non-placeholder `SEED_OWNER_PASSWORD` of at least 14 characters in the deployment secret environment, then run:

```powershell
python -m backend.seed --environment production --bootstrap-owner
```

`--environment` must exactly match configured `ENVIRONMENT`, and `--bootstrap-owner` cannot be combined with `--demo`. A retry succeeds only when the configured email still belongs to the same active owner and the configured password verifies; a different identity, changed secret, disabled/demoted account, or any pre-existing user before first bootstrap is rejected. Remove the bootstrap password from the job environment after verifying owner login. A normal non-demo `python -m backend.seed --environment production` creates only the warehouses.

Start the API:

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal, start the UI:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Development API documentation is at `http://127.0.0.1:8000/docs`; liveness and readiness are at `/api/v1/health/live` and `/api/v1/health/ready`.

## Docker Compose

Copy [.env.example](.env.example) to `.env`, choose a URL-safe database password, a random JWT secret, and a development seed password, then run:

```powershell
docker compose up --build
```

Compose applies migrations and idempotently seeds the two warehouses plus development users before starting the API. The UI is served at `http://localhost:8080`, and the API at `http://localhost:8000`.

## Verification

Backend and browser tests are destructive and deliberately refuse databases whose names are not marked with a distinct `test` segment. Never point `TEST_DATABASE_URL` or `E2E_DATABASE_URL` at development or production data. The Playwright suite rebuilds its database through Alembic and seeds deterministic demo users before starting the API.

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://wms_test:replace-with-test-password@127.0.0.1:5432/whitfield_wms_test'
python -m pytest backend\tests -q
python -m ruff check backend
python -m ruff format --check backend
python -m mypy backend --no-incremental
python -m alembic -c backend\alembic.ini check

cd frontend
npm run lint
npm test
npm run build
npm audit --omit=dev

$env:E2E_DATABASE_URL='postgresql+asyncpg://wms_test:replace-with-test-password@127.0.0.1:5432/whitfield_wms_e2e_test'
npm run test:e2e:install
npm run test:e2e
```

The PostgreSQL suite covers authentication and warehouse isolation, combined and warehouse-specific dashboards, receipt posting/replay, idempotency mismatch, damaged stock, multi-line allocation rollback, two-session oversell contention, reference races, reservation release, the pack/label/ship lifecycle, and append-only database triggers. The Chromium acceptance suite proves owner combined/warehouse switching, staff permission-aware navigation, and a tablet-sized live receiving flow whose corrected accepted quantity appears in inventory.

## Optional low-stock scheduler

The API can run the read-only low-stock count in-process. It is disabled by default. Set `LOW_STOCK_SCHEDULER_ENABLED=true`; the interval defaults to 900 seconds and is validated from 60 through 86,400 seconds. `LOW_STOCK_SCHEDULER_RUN_IMMEDIATELY=true` runs once after process startup, then waits the configured interval after each completed attempt. Setting it to `false` waits one interval before the first check.

Every attempt receives a unique job ID and logs only aggregate warehouse/low-stock counts. A failed attempt is contained and the next interval still runs; API shutdown cancels and awaits an active scheduler task. Because this is an in-process scheduler, every API worker runs its own read-only check. Keep it disabled on all but one worker or use an external scheduler when a deployment requires exactly one execution stream.

## Operating rules

- Receipt drafts never change stock; only **Complete receiving** posts accepted units.
- Damaged quantities never enter sellable inventory.
- Every critical command requires an `Idempotency-Key`; retry the same payload with the same key.
- Orders reserve every line in one warehouse or reserve nothing.
- Shipping consumes both `on_hand` and `reserved`; cancellation releases only active reservations.
- Do not edit or delete `audit_logs` or `inventory_movements`.
- Enter real opening stock through the owner-only opening-balance endpoint with a verified reason; do not change balance rows manually.

## Repository map

- `backend/core/apis`: FastAPI routes and public schemas
- `backend/core/controllers`: access and read orchestration
- `backend/core/services`: transactional receiving, inventory, and fulfillment workflows
- `backend/core/cruds`: persistence operations
- `backend/core/models`: SQLAlchemy models and constraints
- `backend/alembic`: versioned PostgreSQL migrations
- `backend/tests`: real-PostgreSQL integration/concurrency/security tests
- `frontend/src/features`: live React workflow pages
- `frontend/src/lib/api/client.ts`: typed frontend HTTP boundary
- `frontend/e2e`: guarded Playwright startup and live browser acceptance flows
- `docs/production-readiness.md`: external integration and deployment gates
