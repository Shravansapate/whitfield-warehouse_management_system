import { afterEach, describe, expect, it, vi } from "vitest";
import { apiPageRequest, apiRequest, authApi, storeTokens, wmsApi } from "./client";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers }
  });
}

describe("typed API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("uses the documented JSON login contract and normalizes the user", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      access_token: "access-token",
      refresh_token: "refresh-token",
      user: {
        id: "user-1",
        name: "Maya Patel",
        email: "maya@example.com",
        role: "manager",
        is_active: true,
        warehouses: [{ id: "reno-id", code: "RNO", name: "Reno" }]
      }
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await authApi.login("maya@example.com", "correct-password");

    expect(result.accessToken).toBe("access-token");
    expect(result.user?.warehouses[0]).toEqual({ id: "reno-id", code: "RNO", name: "Reno" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/login", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ email: "maya@example.com", password: "correct-password" })
    }));
  });

  it("attaches bearer auth and exposes a stable safe conflict error", async () => {
    storeTokens("access-token");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "Order cannot transition from shipped", code: "ILLEGAL_TRANSITION", request_id: "req-1" }, 409));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/orders/order-1/ship", { method: "POST", json: {} })).rejects.toEqual(expect.objectContaining({
      status: 409,
      code: "ILLEGAL_TRANSITION",
      requestId: "req-1"
    }));
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer access-token");
  });

  it("preserves the backend's nested structured error message and code", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      detail: { detail: "Owner accounts are not warehouse-restricted", code: "OWNER_ASSIGNMENT_FORBIDDEN" }
    }, 409)));

    await expect(apiRequest("/users/user-1/warehouse-assignment", { method: "PUT", json: { warehouse_id: "reno-id" } }))
      .rejects.toEqual(expect.objectContaining({
        message: "Owner accounts are not warehouse-restricted",
        code: "OWNER_ASSIGNMENT_FORBIDDEN"
      }));
  });

  it("captures cursor metadata without changing ordinary API response bodies", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: "one" }], 200, { "X-Next-Cursor": "opaque-next" }))
      .mockResolvedValueOnce(jsonResponse([{ id: "two" }]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiPageRequest<{ id: string }>("/inventory?limit=1")).resolves.toEqual({
      items: [{ id: "one" }],
      nextCursor: "opaque-next"
    });
    await expect(apiRequest<Array<{ id: string }>>("/inventory?limit=1")).resolves.toEqual([{ id: "two" }]);
  });

  it("builds filtered cursor requests for the remaining list resources", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: "warehouse-1", code: "RNO", name: "Reno" }], 200, { "X-Next-Cursor": "warehouse-next" }))
      .mockResolvedValueOnce(jsonResponse([{ id: "balance-1", product_id: "product-1", name: "Lock", available: 8 }], 200, { "X-Next-Cursor": "inventory-next" }))
      .mockResolvedValueOnce(jsonResponse([{ id: "balance-2", product_id: "product-2", name: "Hinge", available: 2 }]))
      .mockResolvedValueOnce(jsonResponse([{ id: "movement-1", movement_type: "RECEIPT", created_at: "2026-08-13T10:00:00Z" }]))
      .mockResolvedValueOnce(jsonResponse([{ id: "user-1", name: "Maya", email: "maya@example.com", role: "manager", is_active: true }]))
      .mockResolvedValueOnce(jsonResponse([{ id: "product-1", sku: "WF-1", upc: "101", name: "Lock", is_active: false }], 200, { "X-Next-Cursor": "product-next" }));
    vi.stubGlobal("fetch", fetchMock);

    const warehouses = await wmsApi.getWarehousesPage({ cursor: "w-cursor", limit: 5, sort: "created_at_asc", is_active: false });
    const inventory = await wmsApi.getInventoryPage("warehouse-1", { q: "lock", cursor: "i-cursor", limit: 10, sort: "available_desc" });
    const lowStock = await wmsApi.getLowStockPage("warehouse-1", { q: "hinge", limit: 3, sort: "name_asc" });
    const movements = await wmsApi.getInventoryMovementsPage("warehouse-1", "product/1", {
      movement_type: "RECEIPT",
      created_from: "2026-08-01T00:00:00Z",
      created_to: "2026-08-31T23:59:59Z",
      cursor: "m-cursor",
      limit: 4,
      sort: "created_at_asc"
    });
    const users = await wmsApi.getUsersPage({
      q: "maya",
      role: "manager",
      is_active: true,
      warehouse_id: "warehouse-1",
      created_from: "2026-01-01T00:00:00Z",
      created_to: "2026-12-31T23:59:59Z",
      cursor: "u-cursor",
      limit: 6,
      sort: "created_at_desc"
    });
    const products = await wmsApi.searchProductsPage({
      q: "lock",
      is_active: false,
      created_from: "2026-01-01T00:00:00Z",
      created_to: "2026-12-31T23:59:59Z",
      cursor: "p-cursor",
      limit: 7,
      sort: "created_at_asc"
    });

    expect(warehouses).toEqual({ items: [{ id: "warehouse-1", code: "RNO", name: "Reno" }], nextCursor: "warehouse-next" });
    expect(inventory.nextCursor).toBe("inventory-next");
    expect(lowStock.items[0].name).toBe("Hinge");
    expect(movements.items[0].movementType).toBe("RECEIPT");
    expect(users.items[0]).toEqual(expect.objectContaining({ name: "Maya", role: "manager", state: "Active" }));
    expect(products).toEqual({
      items: [expect.objectContaining({ id: "product-1", isActive: false })],
      nextCursor: "product-next"
    });

    const requestUrls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(requestUrls).toEqual([
      "/api/v1/warehouses?cursor=w-cursor&limit=5&sort=created_at_asc&is_active=false",
      "/api/v1/inventory?warehouse_id=warehouse-1&q=lock&cursor=i-cursor&limit=10&sort=available_desc",
      "/api/v1/inventory/low-stock?warehouse_id=warehouse-1&q=hinge&limit=3&sort=name_asc",
      "/api/v1/inventory/product%2F1/movements?warehouse_id=warehouse-1&movement_type=RECEIPT&created_from=2026-08-01T00%3A00%3A00Z&created_to=2026-08-31T23%3A59%3A59Z&cursor=m-cursor&limit=4&sort=created_at_asc",
      "/api/v1/users?q=maya&role=manager&is_active=true&warehouse_id=warehouse-1&created_from=2026-01-01T00%3A00%3A00Z&created_to=2026-12-31T23%3A59%3A59Z&cursor=u-cursor&limit=6&sort=created_at_desc",
      "/api/v1/products/search?q=lock&is_active=false&created_from=2026-01-01T00%3A00%3A00Z&created_to=2026-12-31T23%3A59%3A59Z&cursor=p-cursor&limit=7&sort=created_at_asc"
    ]);
  });

  it("keeps existing list methods array-shaped while accepting page filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{
      id: "balance-1",
      product_id: "product-1",
      name: "Lock",
      available: 8
    }], 200, { "X-Next-Cursor": "ignored-by-array-wrapper" }));
    vi.stubGlobal("fetch", fetchMock);

    const inventory = await wmsApi.getInventory("warehouse-1", { q: "lock", limit: 1 });

    expect(Array.isArray(inventory)).toBe(true);
    expect(inventory[0]).toEqual(expect.objectContaining({ productId: "product-1", name: "Lock" }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/inventory?warehouse_id=warehouse-1&q=lock&limit=1",
      expect.any(Object)
    );
  });

  it("captures cursor pages and filters for orders, receiving, returns, and audit", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{ id: "receipt-1", warehouse_id: "warehouse-1", sender_name: "Supplier", status: "received" }], 200, { "X-Next-Cursor": "receipt-next" }))
      .mockResolvedValueOnce(jsonResponse([{ id: "return-1", receipt_id: "receipt-1", product_name: "Lock", quantity: 1, status: "pending_return" }]))
      .mockResolvedValueOnce(jsonResponse([{ id: "order-1", warehouse_id: "warehouse-1", external_reference: "WEB-1", status: "shipped", items: [] }], 200, { "X-Next-Cursor": "order-next" }))
      .mockResolvedValueOnce(jsonResponse([{ id: "audit-1", actor_name: "Maya", action: "order_shipped", source: "web", record_id: "order-1", created_at: "2026-08-13T10:00:00Z" }], 200, { "X-Next-Cursor": "audit-next" }));
    vi.stubGlobal("fetch", fetchMock);

    const receipts = await wmsApi.getReceiptsPage("warehouse-1", { status: "received", created_from: "2026-08-01T00:00:00Z", cursor: "r-cursor", limit: 5, sort: "created_at_asc" });
    const returns = await wmsApi.getDamagedReturnsPage("warehouse-1", { status: "pending_return", created_to: "2026-08-31T23:59:59Z", cursor: "d-cursor", limit: 6 });
    const orders = await wmsApi.getOrdersPage("warehouse-1", { status: "shipped", cursor: "o-cursor", limit: 7, sort: "created_at_desc" });
    const audit = await wmsApi.getAuditLogsPage("warehouse-1", { table_name: "orders", record_id: "order-1", action: "order_shipped", source: "web", cursor: "a-cursor", limit: 8 });

    expect(receipts.nextCursor).toBe("receipt-next");
    expect(returns.items[0].status).toBe("pending_return");
    expect(orders.nextCursor).toBe("order-next");
    expect(audit).toEqual({ items: [expect.objectContaining({ id: "audit-1", action: "order shipped" })], nextCursor: "audit-next" });
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/v1/inbound-receipts?warehouse_id=warehouse-1&status=received&created_from=2026-08-01T00%3A00%3A00Z&cursor=r-cursor&limit=5&sort=created_at_asc",
      "/api/v1/damaged-returns?warehouse_id=warehouse-1&status=pending_return&created_to=2026-08-31T23%3A59%3A59Z&cursor=d-cursor&limit=6",
      "/api/v1/orders?warehouse_id=warehouse-1&status=shipped&cursor=o-cursor&limit=7&sort=created_at_desc",
      "/api/v1/audit-logs?warehouse_id=warehouse-1&table_name=orders&record_id=order-1&action=order_shipped&source=web&cursor=a-cursor&limit=8"
    ]);
  });

  it("normalizes picked quantities and sends retry-safe item confirmations", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{
        id: "order-1",
        warehouse_id: "reno-id",
        external_reference: "WEB-1",
        status: "picking",
        items: [{ id: "item-1", product_id: "product-1", sku: "WF-1", product_name: "Lock", quantity: 3, picked_quantity: 2 }]
      }]))
      .mockResolvedValueOnce(jsonResponse({ detail: "Picked quantity confirmed" }));
    vi.stubGlobal("fetch", fetchMock);

    const orders = await wmsApi.getOrders("reno-id");
    expect(orders[0].items[0]).toEqual(expect.objectContaining({ quantity: 3, pickedQuantity: 2 }));

    await wmsApi.confirmPickedItem("order-1", "item-1", 3, "pick-key-1");
    expect(fetchMock).toHaveBeenLastCalledWith("/api/v1/orders/order-1/items/item-1/pick", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ picked_quantity: 3 })
    }));
    const headers = fetchMock.mock.calls[1][1].headers as Headers;
    expect(headers.get("Idempotency-Key")).toBe("pick-key-1");
  });
});
