# Whitfield Fulfillment WMS - Final Implementation Plan v2

**Status:** Approved planning baseline - ready for phased implementation  
**Prepared from:** Whitfield client case study, the earlier implementation plan, the client's final business answers, and the local Eigi backend/frontend Codex skills  
**Primary objective:** Replace the two warehouse Excel workflows with a secure, auditable WMS that prevents duplicate receiving, overselling, unauthorized cross-warehouse access, and untraceable changes.

---

## 1. Final client decisions

These decisions are fixed requirements unless the owner later requests a documented change.

| Topic | Final decision | System consequence |
|---|---|---|
| Inventory ownership | Inventory belongs to the warehouse, not to individual sellers | Do not add seller/client ownership to inventory balances |
| Cross-warehouse access | Only the `owner` can see or change data across both warehouses | Every non-owner query and write is restricted to the user's assigned warehouse |
| Stock availability | Good stock becomes available as soon as the shipment is fully received in the warehouse | Scanned lines are drafts; accepted stock is posted atomically when the receipt is finalized |
| Damaged goods | Return damaged goods to the sender | Damaged quantity never enters sellable inventory; the return is tracked separately |
| Order contents | One order can contain many products | Use `orders` and `order_items`, not one `product_id` on `orders` |
| Split fulfillment | Not allowed | Every item in an order must be fulfilled completely from one warehouse |
| Cross-warehouse fallback | Not allowed | Do not automatically fulfill from the other warehouse; only the owner may deliberately move or recreate work there |
| Cancellation | Keep the order as `cancelled` in the database | Never delete cancelled orders; release any reservation and audit the cancellation |
| Bin/aisle/shelf tracking | Not confirmed | Out of MVP scope; schema should permit a future location module without redesigning inventory history |
| Carrier and label integration | Required | Use a provider adapter; exact carrier/provider credentials and selection remain deployment configuration |
| Weight and dimensions | Required | Store package weight and dimensions before label creation |
| Audit retention | Retain the complete history from the beginning | Audit and inventory movement records are append-only and have no normal deletion workflow |
| Excel import | Not required | Start with clean master data and opening inventory entered through a controlled opening-balance workflow |
| Recovery-time question | Excluded at the client's request | Do not make an RTO promise in this plan; backups and restore verification remain technical safeguards |

### Clarification applied to "received"

An inbound shipment is considered received only after a staff member finishes scanning and explicitly selects **Complete receiving**. At that moment, one database transaction:

1. validates the receipt and quantities;
2. posts accepted units to sellable inventory;
3. records damaged units for return to sender;
4. writes inventory movement and audit records; and
5. changes the receipt status to `received`.

This prevents a half-scanned shipment from becoming available for orders.

---

## 2. Scope

### 2.1 MVP included

- Two warehouses: Reno and Columbus.
- Product master with SKU, UPC and barcode search.
- Warehouse-owned inventory balances.
- Inbound shipment receiving using carrier tracking number or drop-off ticket number.
- Multi-line receiving and barcode scanning.
- Accepted versus damaged quantity handling.
- Return-to-sender tracking for damaged goods.
- Multi-product orders fulfilled from exactly one warehouse.
- Inventory reservation, picking, packing, weight, dimensions, label creation and shipment.
- Order cancellation with reservation release.
- Owner, manager, trusted staff and new-hire/staff access levels.
- Strict warehouse scoping for every non-owner.
- Full audit history and inventory movement ledger.
- Manager and staff browser application.
- Read-only assistant queries and carefully confirmed voice receiving actions.
- Scheduled low-stock checks.
- PostgreSQL concurrency and idempotency protections.

### 2.2 Explicitly out of MVP scope

- Seller-owned or consignment inventory accounting.
- Splitting one order across warehouses.
- Automatic fallback to another warehouse.
- Bin, aisle, shelf or put-away optimization.
- Excel history migration.
- Purchasing, supplier invoicing or accounting.
- Returns from outbound customers unless later requested.
- Autonomous AI inventory adjustments or autonomous order shipment.
- A contractual recovery-time target.

---

## 3. Problem-to-solution traceability

| Client problem | Root cause | Final solution | Proof |
|---|---|---|---|
| A frozen laptop caused a shipment to be entered twice | Retried write had no stable identity | Required idempotency key and request hash on receipt-line and receipt-finalization commands | Retry tests show inventory increases once |
| Two orders were confirmed for the same nine units | Read-check-write race | Conditional atomic reservation/update in PostgreSQL | Real concurrent test yields exactly one allocation |
| Nobody could answer who changed stock | Spreadsheet had no immutable history | Append-only audit log plus inventory movement ledger in the same transaction | Audit query identifies user, action, time, source and before/after values |
| New hires had too much access | No authorization or warehouse boundary | Role permissions plus assigned warehouse scope | Cross-warehouse API and UI tests return `403` |
| Managers needed stock answers without Excel | No reliable read layer | Inventory, order, receiving and audit dashboards | One request/view shows current warehouse state |
| Staff have hands full while scanning | Keyboard-only workflow | Scanner-first responsive UI and confirmed voice input | Mobile/scanner flow passes end-to-end test |
| Routine checks were manual | No automation | Read-only scheduled low-stock job | Job produces owner/manager alerts without changing inventory |

---

## 4. System architecture

```text
Scanner / browser / voice UI
            |
            | HTTPS + bearer JWT + idempotency key on critical commands
            v
FastAPI routes
            v
Controllers: authorization, warehouse scope, state-machine decisions
            v
Services: receiving, reservation, fulfillment, labels, idempotency
            v
CRUD / Unit of Work: SQLAlchemy async transactions
            v
PostgreSQL: balances + immutable movements + audit history

Scheduled worker ---> WMS service layer (read-only low-stock checks)
Carrier adapter ---> external carrier/label provider
Voice server ------> WMS HTTP API only; never direct database access
```

### Architectural rules

1. PostgreSQL is the only inventory source of truth.
2. No browser, voice service, scheduler or carrier integration accesses the database directly.
3. Routes remain thin and follow the Eigi flow: `route -> controller -> service/CRUD -> database/provider`.
4. Every request gets its own SQLAlchemy `AsyncSession`; sessions are never shared between concurrent tasks.
5. One unit of work owns each business transaction.
6. Balance, movement and audit changes either all commit or all roll back.
7. All timestamps use timezone-aware UTC in storage and are formatted for the user's timezone in the UI.
8. All public API routes are versioned under `/api/v1`.

---

## 5. Final technology stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Pydantic v2 |
| Database | PostgreSQL |
| ORM/driver | SQLAlchemy 2 async + asyncpg |
| Migrations | Alembic |
| Authentication | OAuth2 bearer flow, PyJWT, `pwdlib[argon2]` |
| Backend tests | pytest, pytest-asyncio, HTTPX, real PostgreSQL integration database |
| Frontend | React + TypeScript + Vite |
| Frontend data/API | Central typed API client; TanStack Query is recommended |
| Frontend tests | Vitest + Testing Library; Playwright for critical flows |
| Voice | Separate HTTP client service; provider choices from the existing Phase 6 plan |
| Scheduling | Application worker/scheduler calling read-only domain services |
| Deployment | Docker images and Docker Compose for local/test; environment-driven production deployment |
| Quality | Ruff, mypy/pyright, ESLint, TypeScript typecheck and automated tests |

Real secrets must never be placed in code, examples, logs or version control.

---

## 6. Roles, permissions and warehouse scope

### 6.1 Roles

- `owner`: Dan or a designated business owner. Can access both warehouses, manage users, see all audit data, configure thresholds and perform controlled warehouse transfers.
- `manager`: Restricted to one assigned warehouse. Can manage that warehouse's operational data, audit records and staff work.
- `trusted`: Restricted to one assigned warehouse. Can receive, fulfill and perform reasoned stock adjustments.
- `staff`: Restricted to one assigned warehouse. Can receive, pick, pack and ship through normal workflows, but cannot make direct stock adjustments or see audit history.

`staff` represents the earlier `new_hire` concept with a clearer operational name.

### 6.2 Permission matrix

| Action | Staff | Trusted | Manager | Owner |
|---|:---:|:---:|:---:|:---:|
| Work in assigned warehouse | Yes | Yes | Yes | Yes |
| See another warehouse | No | No | No | Yes |
| Create and complete inbound receipt | Yes | Yes | Yes | Yes |
| Record damaged units | Yes | Yes | Yes | Yes |
| Complete damaged return | No | Yes | Yes | Yes |
| Create/process/cancel order in assigned warehouse | Yes | Yes | Yes | Yes |
| Manual inventory adjustment | No | Yes | Yes | Yes |
| View audit log | No | No | Own warehouse | All warehouses |
| Manage products and thresholds | No | No | Own warehouse thresholds only | Full |
| Create, disable or change users | No | No | No | Yes |
| Transfer inventory between warehouses | No | No | No | Yes |

### 6.3 Enforcement rules

- A non-owner token is never trusted for a caller-supplied `warehouse_id` without checking the user's assignment.
- List queries automatically filter by assigned warehouse.
- Resource lookups returning another warehouse's record respond with `403` rather than exposing its contents.
- `get_current_user` returns `401` for missing, invalid or expired credentials.
- `require_permission` returns `403` for an authenticated user lacking role or warehouse permission.
- User creation is owner-only; there is no public signup endpoint.
- Disabling a user keeps their audit history intact.

---

## 7. Data model

All primary keys should be UUIDs. All mutable business tables include `created_at`, `updated_at` and an optimistic/version field where useful. Foreign keys default to `ON DELETE RESTRICT` for historical records.

### 7.1 Master and access tables

#### `warehouses`

- `id`, `code`, `name`, `location`, `is_active`, timestamps.
- Unique `code` and `name`.
- Initial rows: Reno and Columbus.

#### `users`

- `id`, `name`, `email`, `hashed_password`, `role`, `is_active`, timestamps.
- Unique normalized email.
- Password hashes never appear in response or audit snapshots.

#### `user_warehouse_assignments`

- `user_id`, `warehouse_id`, `created_at`.
- Unique pair.
- Non-owner active users must have exactly one active warehouse assignment at the application level.
- Owner may operate across both warehouses without assignment filtering.

#### `products`

- `id`, `sku`, `upc`, `name`, `description`, `is_active`, timestamps.
- SKU and UPC are globally unique because inventory is warehouse-owned rather than seller-owned.
- UPC is indexed for scanner lookup.

#### `warehouse_product_settings`

- `warehouse_id`, `product_id`, `low_stock_threshold`, timestamps.
- Allows Reno and Columbus to use different thresholds for the same product.

### 7.2 Inventory tables

#### `inventory_balances`

- `id`, `warehouse_id`, `product_id`, `on_hand`, `reserved`, `updated_at`.
- Unique `(warehouse_id, product_id)`.
- Checks: `on_hand >= 0`, `reserved >= 0`, `reserved <= on_hand`.
- Sellable availability is `on_hand - reserved`.
- Damaged stock is not included in `on_hand` because the approved disposition is return to sender.

#### `inventory_movements`

- `id`, `warehouse_id`, `product_id`, `movement_type`, `on_hand_delta`, `reserved_delta`, `reference_type`, `reference_id`, `actor_user_id`, `source`, `reason`, `on_hand_after`, `reserved_after`, `created_at`.
- Movement types: `OPENING_BALANCE`, `RECEIPT`, `RESERVE`, `RELEASE`, `SHIP`, `ADJUST`, `TRANSFER_OUT`, `TRANSFER_IN`.
- Append-only. No application update/delete endpoint.
- Every inventory balance change creates a movement in the same transaction.

#### `inventory_adjustments`

- `id`, `warehouse_id`, `product_id`, `quantity_delta`, `reason`, `status`, `created_by`, timestamps.
- Only trusted, manager or owner.
- Zero adjustments are rejected and a reason is mandatory.

### 7.3 Inbound receiving tables

#### `inbound_receipts`

- `id`, `warehouse_id`, `tracking_number`, `ticket_number`, `sender_name`, `sender_contact`, `sender_return_address`, `status`, `created_by`, `received_by`, `received_at`, timestamps.
- At least one of tracking number or ticket number is required.
- Status: `open`, `receiving`, `received`, `cancelled`.
- A received receipt cannot be edited or received again.

#### `inbound_receipt_items`

- `id`, `receipt_id`, `product_id`, `quantity_received`, `quantity_accepted`, `quantity_damaged`, `damage_notes`, timestamps.
- Checks: quantities are non-negative and `accepted + damaged = received`.
- Items remain draft data until receipt finalization.
- Multiple scans of the same product may be consolidated by the service while preserving command/audit history.

#### `damaged_returns`

- `id`, `receipt_id`, `receipt_item_id`, `warehouse_id`, `product_id`, `quantity`, `status`, `return_tracking_number`, `returned_at`, `handled_by`, notes, timestamps.
- Status: `pending_return`, `returned_to_sender`, `cancelled`.
- A damaged return does not modify sellable inventory because damaged units were never added.

### 7.4 Outbound order tables

#### `orders`

- `id`, `external_reference`, `warehouse_id`, `status`, `created_by`, `cancelled_by`, `cancel_reason`, timestamps.
- Status: `pending`, `allocated`, `picking`, `packed`, `label_created`, `shipped`, `cannot_fulfill`, `cancelled`.
- An order belongs to exactly one warehouse.
- It is never silently moved or split across warehouses.

#### `order_items`

- `id`, `order_id`, `product_id`, `quantity`, timestamps.
- Positive quantity.
- Unique `(order_id, product_id)` after request consolidation.

#### `inventory_reservations`

- `id`, `order_id`, `order_item_id`, `warehouse_id`, `product_id`, `quantity`, `status`, timestamps.
- Status: `active`, `released`, `consumed`.
- One active reservation per order item.

#### `outbound_packages`

- `id`, `order_id`, `weight`, `weight_unit`, `length`, `width`, `height`, `dimension_unit`, `carrier`, `service_level`, `tracking_number`, `label_url_or_key`, `status`, timestamps.
- Weight and every dimension must be positive before requesting a label.
- MVP normally uses one package per order; structure permits multiple packages later without splitting inventory fulfillment across warehouses.

### 7.5 Reliability and audit tables

#### `idempotency_records`

- `id`, `user_id`, `operation`, `idempotency_key`, `request_hash`, `resource_type`, `resource_id`, `response_status`, `response_body`, `created_at`, `expires_at`.
- Unique `(user_id, operation, idempotency_key)`.
- Same key + same request returns the stored result.
- Same key + different request returns `409 Conflict`.
- Required for receipt item writes, receipt finalization, order creation, cancellation, adjustments and voice writes.

#### `audit_logs`

- `id`, `actor_user_id`, `warehouse_id`, `table_name`, `record_id`, `action`, `before_value`, `after_value`, `request_id`, `source`, `reason`, `created_at`.
- Source: `web`, `scanner`, `voice`, `automation`, `api`, `system`.
- Append-only with indexes on `(table_name, record_id)`, `(warehouse_id, created_at)` and `(actor_user_id, created_at)`.
- Complete retention from system start; no normal purge endpoint.
- Failed authentication events may be stored in a separate security log because no valid actor may exist.

---

## 8. Core transactional workflows

### 8.1 Finalize an inbound receipt

`POST /api/v1/inbound-receipts/{id}/receive`

Within one transaction:

1. Acquire/validate the idempotency record.
2. Lock the receipt and verify it is `open` or `receiving`.
3. Verify at least one line and validate `accepted + damaged = received` for every line.
4. For each accepted quantity, atomically upsert `inventory_balances.on_hand`.
5. Create a `RECEIPT` inventory movement for every accepted line.
6. Create `pending_return` damaged-return rows for damaged quantities.
7. Mark the receipt `received` and set actor/time.
8. Write audit records.
9. Store the idempotent response and commit once.

If any step fails, nothing is posted. A retry returns the original successful response and cannot double stock.

### 8.2 Allocate a multi-product order

Order allocation is all-or-nothing within its selected warehouse.

1. Consolidate duplicate product lines.
2. Sort product IDs to use deterministic update order and reduce deadlock risk.
3. Begin one outer transaction, insert/flush the pending order, and open a database savepoint for the allocation attempt.
4. Inside that savepoint, atomically reserve each line only if enough availability exists:

```sql
UPDATE inventory_balances
SET reserved = reserved + :qty,
    updated_at = now()
WHERE warehouse_id = :warehouse_id
  AND product_id = :product_id
  AND (on_hand - reserved) >= :qty
RETURNING on_hand, reserved;
```

5. Create active reservation and movement/audit entries.
6. If every line succeeds, release the savepoint, set the order to `allocated` and commit the outer transaction.
7. If any line fails, roll back the allocation savepoint so every reservation and related movement disappears, set the still-pending order to `cannot_fulfill`, write the failure audit, and commit the outer transaction. Identify shortages in the response.

The service never checks the other warehouse and never partially allocates the order.

### 8.3 Fulfillment state machine

```text
pending -> allocated -> picking -> packed -> label_created -> shipped
   |           |          |         |
   +-----------+----------+---------+----> cancelled
   |
   +----> cannot_fulfill -> cancelled
```

- `allocated` means stock is reserved, not physically packed.
- `packed` requires completion of picking/packing and positive package measurements.
- `label_created` requires a successful carrier adapter response.
- `shipped` consumes reservations and atomically decreases both `on_hand` and `reserved`.
- Illegal transitions return `409 Conflict`.
- A shipped order cannot be cancelled through the normal cancellation endpoint.

### 8.4 Cancel an order

Within one transaction:

1. Lock and validate the order.
2. Reject if already shipped.
3. Release all active reservations using guarded updates.
4. Create `RELEASE` movements.
5. Set status `cancelled`, actor, timestamp and mandatory reason.
6. Write audit records and commit.

The order and its items remain permanently queryable as cancelled.

### 8.5 Ship an order

Within one transaction:

1. Require status `label_created` and tracking number.
2. For every reservation, atomically subtract its quantity from both `on_hand` and `reserved`.
3. Mark reservations `consumed`.
4. Create `SHIP` movements.
5. Mark order and package `shipped`.
6. Write audit records and commit.

### 8.6 Damaged return

- Completion requires return tracking/reference and authorized trusted/manager/owner actor.
- Change `pending_return` to `returned_to_sender` and audit it.
- Do not subtract sellable stock because the damaged units never entered `on_hand`.

---

## 9. API contract

All list endpoints support cursor pagination, `limit`, sorting and appropriate warehouse/date/status filters. Non-owner warehouse filtering is applied by the server, not left to the client.

### Authentication and users

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`
- `POST /api/v1/users` - owner only
- `PATCH /api/v1/users/{id}` - owner only
- `POST /api/v1/users/{id}/reset-password` - owner only
- `PUT /api/v1/users/{id}/warehouse-assignment` - owner only

### Products and warehouse settings

- `POST /api/v1/products` - owner
- `GET /api/v1/products/search?q=` - authenticated and warehouse-safe
- `GET /api/v1/products/{id}`
- `PATCH /api/v1/products/{id}` - owner
- `PUT /api/v1/warehouses/{warehouse_id}/products/{product_id}/threshold`

### Inbound receiving

- `POST /api/v1/inbound-receipts`
- `GET /api/v1/inbound-receipts`
- `GET /api/v1/inbound-receipts/{id}`
- `POST /api/v1/inbound-receipts/{id}/items`
- `PATCH /api/v1/inbound-receipts/{id}/items/{item_id}`
- `DELETE /api/v1/inbound-receipts/{id}/items/{item_id}` - draft only
- `POST /api/v1/inbound-receipts/{id}/receive`
- `POST /api/v1/inbound-receipts/{id}/cancel`
- `GET /api/v1/damaged-returns`
- `POST /api/v1/damaged-returns/{id}/complete`

### Inventory

- `GET /api/v1/inventory`
- `GET /api/v1/inventory/{product_id}`
- `GET /api/v1/inventory/{product_id}/movements`
- `POST /api/v1/inventory/adjustments`
- `GET /api/v1/inventory/low-stock`
- `POST /api/v1/inventory/transfers` - owner only, optional late-MVP operation

There is no general public `/inventory/add` or `/inventory/remove`; inventory changes only through controlled business commands.

### Orders, packing and shipping

- `POST /api/v1/orders`
- `GET /api/v1/orders`
- `GET /api/v1/orders/{id}`
- `POST /api/v1/orders/{id}/start-picking`
- `POST /api/v1/orders/{id}/pack`
- `POST /api/v1/orders/{id}/create-label`
- `POST /api/v1/orders/{id}/ship`
- `POST /api/v1/orders/{id}/cancel`

### Owner and manager read layer

- `GET /api/v1/audit-logs`
- `GET /api/v1/dashboard/summary`
- `GET /api/v1/dashboard/receiving-backlog`
- `GET /api/v1/dashboard/fulfillment-backlog`
- `GET /api/v1/health/live`
- `GET /api/v1/health/ready`

### Response conventions

- `200/201`: successful command or query.
- `400`: syntactically understandable but invalid input outside normal schema validation.
- `401`: missing, invalid or expired authentication.
- `403`: authenticated but role/warehouse permission denied.
- `404`: resource does not exist.
- `409`: state conflict, insufficient stock, illegal transition or idempotency mismatch.
- `422`: Pydantic request validation failure.
- `500`: unexpected server failure with a request ID; no internal stack trace in response.

Stable error shape:

```json
{
  "detail": "Human-readable message",
  "code": "STABLE_MACHINE_CODE",
  "request_id": "uuid"
}
```

---

## 10. Frontend application

### Staff experience

- Login and assigned-warehouse indicator.
- Receiving queue and create-receipt form.
- Scanner-focused UPC input with quantity, accepted and damaged controls.
- Large **Complete receiving** confirmation with a full summary.
- Picking queue and item checklist.
- Packing form with required weight and dimensions.
- Label creation, display/reprint and shipment confirmation.
- Order cancellation with mandatory reason.

### Manager experience

- Own-warehouse dashboard.
- On-hand, reserved and available inventory.
- Low-stock list.
- Receiving and fulfillment backlogs.
- Damaged-return queue.
- Inventory movement history.
- Own-warehouse audit history.
- Trusted-user inventory adjustment screen.

### Owner experience

- Combined dashboard with an explicit warehouse selector.
- User creation, role assignment, disablement and warehouse assignment.
- Both-warehouse audit and inventory views.
- Product master and threshold administration.
- Explicit cross-warehouse transfer workflow if enabled.

### UI quality requirements

- Responsive on warehouse tablets and desktop.
- Keyboard and barcode-scanner friendly.
- Loading, empty, error, unauthorized and success states on every workflow.
- Visible warehouse context on every operational page.
- Destructive/irreversible actions require confirmation.
- No raw API errors or secret values rendered.
- Accessible labels, focus states and semantic controls.

---

## 11. Carrier and label integration

The carrier/provider is configurable because the client confirmed integration is required but did not name the provider.

Define a provider-neutral interface:

```text
validate_address()
get_rates()
create_label()
void_label()
track_shipment()
```

Implementation requirements:

- Build a fake provider for local development and automated tests.
- Keep provider credentials in environment/secret storage.
- Apply timeouts and controlled retries only to safe provider operations.
- Store provider request IDs, carrier, service, tracking and label reference.
- Do not mark an order shipped merely because a label was created.
- Normalize provider errors into stable application error codes.
- Add the real carrier adapter after the owner supplies provider choice, account and credentials.

This unresolved provider selection does not block Phases 0-5 because the adapter contract and fake implementation can be completed first.

---

## 12. Voice assistant and automation

### Voice tools

- `search_product`
- `check_inventory`
- `get_receipt_status`
- `add_receipt_item`
- `get_order_status`

### Voice safety rules

- Voice server forwards the user's WMS JWT.
- It cannot bypass role or warehouse scope.
- Every write uses an idempotency key.
- Before a receiving write, read back warehouse, product, accepted quantity and damaged quantity.
- Require explicit confirmation.
- Record audit source as `voice`.
- Receipt finalization remains a separately confirmed command; it is not inferred from silence or conversation ending.
- Voice and chat services never access PostgreSQL directly.

### Automation

- Scheduled low-stock check is read-only.
- Results are scoped: managers receive their warehouse; owner may receive both.
- Job runs are logged with counts and request/job ID.
- Automation cannot adjust stock, receive shipments, cancel orders or ship orders in MVP.

---

## 13. Eigi-compliant repository structure

```text
whitfield-wms/
|-- backend/
|   |-- main.py
|   |-- commons/
|   |   |-- logger.py
|   |   |-- auth.py
|   |   `-- request_context.py
|   |-- core/
|   |   |-- apis/
|   |   |   |-- api.py
|   |   |   |-- routes/
|   |   |   |   |-- auth.py
|   |   |   |   |-- users.py
|   |   |   |   |-- products.py
|   |   |   |   |-- inbound_receipts.py
|   |   |   |   |-- damaged_returns.py
|   |   |   |   |-- inventory.py
|   |   |   |   |-- orders.py
|   |   |   |   |-- audit_logs.py
|   |   |   |   `-- dashboard.py
|   |   |   `-- schemas/
|   |   |       |-- requests/
|   |   |       `-- responses/
|   |   |-- controllers/
|   |   |   |-- auth_controller.py
|   |   |   |-- user_controller.py
|   |   |   |-- receiving_controller.py
|   |   |   |-- inventory_controller.py
|   |   |   |-- order_controller.py
|   |   |   `-- audit_controller.py
|   |   |-- cruds/
|   |   |   |-- user_crud.py
|   |   |   |-- product_crud.py
|   |   |   |-- receipt_crud.py
|   |   |   |-- inventory_crud.py
|   |   |   |-- order_crud.py
|   |   |   |-- idempotency_crud.py
|   |   |   `-- audit_crud.py
|   |   |-- models/
|   |   |   |-- access.py
|   |   |   |-- product.py
|   |   |   |-- inventory.py
|   |   |   |-- receiving.py
|   |   |   |-- order.py
|   |   |   |-- reliability.py
|   |   |   `-- base.py
|   |   |-- services/
|   |   |   |-- receiving_service.py
|   |   |   |-- inventory_service.py
|   |   |   |-- allocation_service.py
|   |   |   |-- fulfillment_service.py
|   |   |   |-- idempotency_service.py
|   |   |   `-- carriers/
|   |   |       |-- base.py
|   |   |       |-- fake_provider.py
|   |   |       `-- provider_factory.py
|   |   |-- database/
|   |   |   |-- engine.py
|   |   |   |-- session.py
|   |   |   `-- unit_of_work.py
|   |   |-- jobs/
|   |   |   `-- low_stock_job.py
|   |   |-- config/
|   |   |-- constants/
|   |   `-- utils/
|   |-- alembic/
|   |-- tests/
|   |   |-- unit/
|   |   |-- integration/
|   |   |-- concurrency/
|   |   |-- contract/
|   |   `-- security/
|   |-- seed.py
|   |-- requirements.txt
|   |-- alembic.ini
|   |-- Dockerfile
|   |-- .env.example
|   `-- .gitignore
|-- frontend/
|   |-- src/
|   |   |-- app/
|   |   |-- features/
|   |   |   |-- auth/
|   |   |   |-- dashboard/
|   |   |   |-- receiving/
|   |   |   |-- damaged-returns/
|   |   |   |-- inventory/
|   |   |   |-- orders/
|   |   |   |-- audit/
|   |   |   |-- users/
|   |   |   `-- assistant/
|   |   |-- components/
|   |   |   |-- ui/
|   |   |   |-- forms/
|   |   |   `-- layout/
|   |   |-- lib/api/
|   |   |-- hooks/
|   |   |-- stores/
|   |   |-- styles/
|   |   |-- types/
|   |   `-- utils/
|   |-- tests/
|   |-- package.json
|   |-- vite.config.ts
|   |-- tsconfig.json
|   |-- Dockerfile
|   |-- .env.example
|   `-- .gitignore
|-- voice-assistant/
|-- docs/
|   |-- architecture-decisions/
|   `-- api/
|-- docker-compose.yml
|-- .github/workflows/
`-- README.md
```

### Layer responsibilities

- **Routes:** paths, dependencies, request/response schemas, logging and error translation only.
- **Controllers:** authorization, warehouse scoping, business validation and orchestration.
- **CRUD:** SQLAlchemy persistence and atomic SQL.
- **Services:** reusable workflows and carrier/voice/provider integration.
- **Models:** database storage shape and constraints.
- **Request/response schemas:** public wire contracts, kept separate from models.
- **Unit of work:** provides one transaction shared by inventory, movement, order and audit CRUD operations.
- **Frontend features:** business UI and workflow state.
- **Frontend API client:** only place that performs raw HTTP calls.

Every new or modified backend function/method requires an Eigi-style docstring, entry logging and safe error handling. Routes re-raise known HTTP errors and convert unknown errors to a consistent `500`. Logs never contain passwords, JWTs, provider secrets or raw sensitive payloads.

---

## 14. Phased implementation and completion gates

### Phase 0 - Foundation

Build repository skeleton, FastAPI app aggregator, React shell, PostgreSQL, configuration, logging, request IDs, health endpoints, Docker Compose and CI.

**Done when:** all services boot; health readiness checks PostgreSQL; lint/typecheck/test commands work from README instructions.

### Phase 1 - Master data and schema

Build warehouses, users, assignments, products, settings, Alembic setup and seed data.

**Done when:** migrations upgrade/downgrade cleanly, bad FKs/constraints fail, Reno and Columbus seed successfully, and no real secrets exist.

### Phase 2 - Authentication, authorization and warehouse isolation

Build login/refresh/logout, owner-managed accounts, permissions and server-side warehouse scope.

**Done when:** unauthenticated is `401`; wrong role or other warehouse is `403`; only owner can query both warehouses or manage users.

### Phase 3 - Inbound receiving

Build receipts, barcode lookup, draft items, accepted/damaged validation, finalization, damaged returns, idempotency and receiving UI.

**Done when:** incomplete receipt adds no stock; finalization adds accepted stock once; damaged stock adds zero; same command retry is a no-op; damaged return is traceable.

### Phase 4 - Inventory engine

Build balances, movements, adjustment workflow, availability queries, thresholds and concurrency protection.

**Done when:** movements reconcile to balances; no constraint permits negative stock; unauthorized adjustment fails; low stock is correct per warehouse.

### Phase 5 - Multi-product orders and reservations

Build orders/items and all-or-nothing single-warehouse allocation.

**Done when:** one order accepts multiple products; one shortage rolls back every reservation; concurrent orders cannot reserve the same stock; no cross-warehouse fallback occurs.

### Phase 6 - Picking, packing, labels, shipping and cancellation

Build fulfillment state machine, package measurements, fake carrier adapter, label workflow, shipment posting and cancellation.

**Done when:** allocated is not treated as packed; measurements are mandatory; cancellation releases stock and remains in DB; shipping consumes stock once; invalid transitions return `409`.

### Phase 7 - Dashboards and operational frontend

Build staff, manager and owner views with scanner/tablet usability.

**Done when:** each role sees only allowed actions/data; all pages have loading/empty/error states; critical receiving and order flows pass Playwright tests.

### Phase 8 - Voice and scheduled checks

Connect assistant through HTTP, confirmation and idempotency; add read-only low-stock scheduling.

**Done when:** voice cannot bypass warehouse scope; confirmed write posts once; ambiguous speech makes no write; automated job changes no business data.

### Phase 9 - Production hardening

Add deployment configuration, monitoring, database backup and restore verification, security checks, performance tests and real carrier adapter when credentials are available.

**Done when:** production checklist passes, restore procedure has been exercised, audit/movement retention is preserved, and carrier sandbox tests pass.

---

## 15. Mandatory test plan

### Backend unit tests

- Schema constraints and validators.
- Permission and warehouse-scope rules.
- State transitions.
- Idempotency request hashing.
- Carrier error normalization.

### PostgreSQL integration tests

- Receipt finalization transaction and rollback.
- Accepted/damaged quantity posting.
- Audit and movement rows in same transaction.
- Cancellation and reservation release.
- Shipment and reservation consumption.
- Append-only audit/movement permissions.

### Required concurrency tests

1. Two orders contend for the same near-empty product: one allocation succeeds, one fails, final reserved quantity is correct.
2. Two multi-line orders acquire products in different request order: deterministic sorting prevents inconsistent partial commits.
3. Two receipt-finalization requests use the same idempotency key: one stock posting and one stored replay.
4. Same idempotency key with different payload: `409`, no change.
5. Cancellation racing with shipping: exactly one legal terminal result; stock and reservation remain consistent.

These tests must run against real PostgreSQL with separate sessions/connections, never SQLite.

### Security tests

- Malformed/expired token -> `401`.
- Correct token, wrong role -> `403`.
- Non-owner tries another warehouse by query, body or resource ID -> `403` and no data leakage.
- Disabled user token fails.
- Owner-only operations reject all other roles.
- Secrets and password hashes never appear in API responses or logs.

### Frontend tests

- Permission-based navigation and controls.
- Scanner form validation and duplicate retry behaviour.
- Receipt completion confirmation.
- Order cancellation and released-stock refresh.
- Loading, empty, error and unauthorized states.
- Responsive receiving and packing flows.

---

## 16. Operational and security requirements

- HTTPS in deployed environments.
- Short-lived access tokens and controlled refresh tokens.
- Argon2 password hashing.
- CORS allow-list, not wildcard in production.
- Rate limiting on login and sensitive commands.
- Parameterized SQL/SQLAlchemy expressions only.
- Structured logs with request ID and stable resource IDs.
- Database credentials use least privilege.
- Migrations run as a controlled deployment step.
- Automated database backups and periodic restore verification.
- Audit and movement tables retained from the beginning with no application deletion API.
- Metrics: request failures/latency, DB pool use, receipt/order failures, carrier failures, scheduler failures and low-stock counts.
- Alerts must not contain credentials or full sensitive payloads.

---

## 17. Seed and opening inventory policy

Because Excel import is not required:

1. Seed only the two warehouses and development/test users in non-production environments.
2. Create real production users through the owner workflow.
3. Create products through the product API/UI.
4. Enter verified starting stock using `OPENING_BALANCE` movements through an owner-only command or controlled one-time script.
5. Require a reason, actor and audit record for every opening balance.
6. Disable the one-time production script after opening balances are accepted.

Do not silently copy spreadsheet quantities into the database.

---

## 18. Codex implementation rules

When handing this document to Codex, use these hard constraints:

1. Build one phase at a time and stop at each completion gate for review.
2. Follow the local Eigi backend and frontend skills and their folder structures.
3. Before writing backend route/controller/CRUD/service modules, read the Eigi examples reference as required by the backend skill.
4. Preserve route -> controller -> service/CRUD layering.
5. Add docstrings and required logging to every backend function and method.
6. Never implement inventory as Python `SELECT -> if -> UPDATE` logic.
7. Never share one `AsyncSession` between concurrent tasks.
8. Never commit inventory without its movement and audit rows in the same transaction.
9. Never add damaged quantity to sellable inventory.
10. Never post draft receipt items to inventory before receipt finalization.
11. Never split or automatically move an order to another warehouse.
12. Never mark allocation as packing or shipment.
13. Never delete a cancelled order, audit row or inventory movement.
14. Never make critical idempotency keys optional.
15. Never use SQLite for transaction/concurrency proof.
16. Never hardcode real secrets or carrier credentials.
17. Do not implement bin-location functionality in MVP.
18. Do not invent an RTO commitment.

---

## 19. Final acceptance checklist

- [ ] Reno and Columbus are independent operational scopes.
- [ ] Only owner can see or change both warehouses.
- [ ] Non-owner access is restricted server-side, not only hidden in the UI.
- [ ] Products are searchable by UPC, SKU and name.
- [ ] A receipt requires tracking or ticket number and sender return information.
- [ ] Draft receiving does not change inventory.
- [ ] Final receiving adds accepted quantity exactly once.
- [ ] Damaged quantity never becomes available and is tracked to return to sender.
- [ ] One order contains many products.
- [ ] One order is fulfilled completely from one warehouse or not allocated.
- [ ] No automatic cross-warehouse fallback exists.
- [ ] Atomic reservation prevents overselling under real concurrency.
- [ ] `allocated`, `picking`, `packed`, `label_created` and `shipped` are distinct.
- [ ] Weight and dimensions are required before label creation.
- [ ] Carrier integration uses an adapter and fake test provider.
- [ ] Cancellation remains visible and releases active reservations.
- [ ] Every stock mutation has a matching movement and audit record.
- [ ] Audit history is retained from system start.
- [ ] Manager sees only assigned-warehouse audit and dashboards.
- [ ] Owner sees combined and warehouse-specific dashboards.
- [ ] Staff UI works with barcode scanners and warehouse tablets.
- [ ] Voice writes require authentication, read-back confirmation and idempotency.
- [ ] Scheduled AI/routine checks are read-only.
- [ ] No Excel import is included.
- [ ] Bin locations remain outside MVP.
- [ ] All required unit, integration, concurrency, security and E2E tests pass.

---

## 20. Final definition of success

The Whitfield WMS is complete when warehouse staff can receive, inspect, pick, pack and ship without Excel; managers can accurately see and audit their assigned warehouse; the owner can oversee both warehouses; duplicate receiving and concurrent overselling are proven impossible by PostgreSQL tests; damaged goods never enter sellable inventory; multi-product orders remain within one warehouse; cancelled orders remain traceable; and every stock change can be explained from immutable movement and audit history.
