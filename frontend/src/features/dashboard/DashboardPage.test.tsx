import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { DashboardMetrics, InventoryRow } from "../../types/wms";
import { DashboardPage } from "./DashboardPage";

const metrics: DashboardMetrics = {
  availableUnits: 12,
  reservedUnits: 2,
  receivingBacklog: 1,
  ordersToShip: 1,
  damagedReturns: 0,
  auditEvents: 4
};

const lowStock: InventoryRow = {
  id: "balance-1",
  productId: "product-1",
  warehouseId: "reno-id",
  sku: "WF-LOCK-114",
  upc: "724880001140",
  name: "Smart deadbolt",
  onHand: 4,
  reserved: 1,
  available: 3,
  threshold: 4
};

describe("DashboardPage low-stock paging", () => {
  it("loads the next low-stock cursor page and retains earlier signals", async () => {
    const user = userEvent.setup();
    vi.spyOn(wmsApi, "getLowStockPage")
      .mockResolvedValueOnce({ items: [lowStock], nextCursor: "low-stock-next" })
      .mockResolvedValueOnce({
        items: [{ ...lowStock, id: "balance-2", productId: "product-2", sku: "WF-HINGE-200", name: "Door hinge" }],
        nextCursor: null
      });

    render(
      <DashboardPage
        error={null}
        metrics={metrics}
        onNavigate={vi.fn()}
        onRetry={vi.fn()}
        refreshVersion={0}
        status="success"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await screen.findByText("Smart deadbolt");
    expect(screen.getByText("1+")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more low-stock products" }));

    await waitFor(() => expect(wmsApi.getLowStockPage).toHaveBeenLastCalledWith("reno-id", { cursor: "low-stock-next" }));
    expect(screen.getByText("Smart deadbolt")).toBeInTheDocument();
    expect(await screen.findByText("Door hinge")).toBeInTheDocument();
  });
});
