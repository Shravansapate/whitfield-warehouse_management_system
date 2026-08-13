import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { InventoryRow } from "../../types/wms";
import { InventoryPage } from "./InventoryPage";

const inventory: InventoryRow = {
  id: "balance-1",
  productId: "product-1",
  warehouseId: "reno-id",
  sku: "WF-LOCK-114",
  upc: "724880001140",
  name: "Smart deadbolt",
  onHand: 12,
  reserved: 2,
  available: 10,
  threshold: 4
};

describe("InventoryPage manager controls", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getInventoryPage").mockResolvedValue({ items: [inventory], nextCursor: null });
    vi.spyOn(wmsApi, "getInventoryMovementsPage").mockResolvedValue({ items: [{
      id: "movement-1",
      movementType: "RECEIPT",
      onHandDelta: 5,
      reservedDelta: 0,
      onHandAfter: 12,
      reservedAfter: 2,
      reason: "Inbound receipt",
      createdAt: "2026-08-13T12:00:00Z"
    }], nextCursor: null });
    vi.spyOn(wmsApi, "setProductThreshold").mockResolvedValue({
      warehouse_id: "reno-id",
      product_id: "product-1",
      low_stock_threshold: 8
    });
    vi.spyOn(wmsApi, "postOpeningBalance").mockResolvedValue({});
  });

  it("lets a manager update only the selected warehouse threshold", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    vi.spyOn(window, "prompt").mockReturnValue("8");
    render(
      <InventoryPage
        onChanged={changed}
        refreshVersion={0}
        role="manager"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "Set threshold for WF-LOCK-114" }));

    await waitFor(() => expect(wmsApi.setProductThreshold).toHaveBeenCalledWith("reno-id", "product-1", 8));
    expect(changed).toHaveBeenCalledOnce();
    expect(await screen.findByRole("status")).toHaveTextContent("Reno threshold for WF-LOCK-114 is now 8");
  });

  it("does not expose threshold controls to staff", async () => {
    render(
      <InventoryPage
        onChanged={vi.fn()}
        refreshVersion={0}
        role="staff"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await screen.findByText("WF-LOCK-114");
    expect(screen.queryByRole("button", { name: "Set threshold for WF-LOCK-114" })).not.toBeInTheDocument();
  });

  it("posts a verified owner opening balance through an idempotent command", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <InventoryPage
        onChanged={vi.fn()}
        refreshVersion={0}
        role="owner"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "Set opening balance" }));
    await user.selectOptions(screen.getByLabelText("Product"), "product-1");
    await user.type(screen.getByLabelText("Opening quantity"), "25");
    await user.type(screen.getByLabelText("Verification reason"), "Verified physical count");
    await user.click(screen.getByRole("button", { name: "Post opening balance" }));

    await waitFor(() => expect(wmsApi.postOpeningBalance).toHaveBeenCalledWith({
      warehouse_id: "reno-id",
      product_id: "product-1",
      quantity: 25,
      reason: "Verified physical count"
    }, expect.stringMatching(/^opening-balance-/)));
  });

  it("loads immutable movement history for the selected warehouse product", async () => {
    const user = userEvent.setup();
    render(
      <InventoryPage
        onChanged={vi.fn()}
        refreshVersion={0}
        role="manager"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "View movements for WF-LOCK-114" }));

    await waitFor(() => expect(wmsApi.getInventoryMovementsPage).toHaveBeenCalledWith("reno-id", "product-1", { cursor: undefined }));
    expect(await screen.findByText("Inbound receipt")).toBeInTheDocument();
    expect(screen.getByText("12 on hand / 2 reserved")).toBeInTheDocument();
  });

  it("loads older immutable movements with the returned cursor", async () => {
    const user = userEvent.setup();
    vi.mocked(wmsApi.getInventoryMovementsPage)
      .mockResolvedValueOnce({
        items: [{ id: "movement-1", movementType: "RECEIPT", onHandDelta: 5, reservedDelta: 0, onHandAfter: 12, reservedAfter: 2, reason: "Inbound receipt", createdAt: "2026-08-13T12:00:00Z" }],
        nextCursor: "movements-next"
      })
      .mockResolvedValueOnce({
        items: [{ id: "movement-2", movementType: "OPENING_BALANCE", onHandDelta: 7, reservedDelta: 0, onHandAfter: 7, reservedAfter: 0, reason: "Verified opening count", createdAt: "2026-08-01T12:00:00Z" }],
        nextCursor: null
      });
    render(
      <InventoryPage
        onChanged={vi.fn()}
        refreshVersion={0}
        role="manager"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await user.click(await screen.findByRole("button", { name: "View movements for WF-LOCK-114" }));
    await screen.findByText("Inbound receipt");
    await user.click(screen.getByRole("button", { name: "Load more movements" }));

    await waitFor(() => expect(wmsApi.getInventoryMovementsPage).toHaveBeenLastCalledWith("reno-id", "product-1", { cursor: "movements-next" }));
    expect(screen.getByText("Inbound receipt")).toBeInTheDocument();
    expect(await screen.findByText("Verified opening count")).toBeInTheDocument();
  });

  it("appends the next inventory cursor page without hiding the first page", async () => {
    const user = userEvent.setup();
    vi.mocked(wmsApi.getInventoryPage)
      .mockResolvedValueOnce({ items: [inventory], nextCursor: "inventory-next" })
      .mockResolvedValueOnce({
        items: [{ ...inventory, id: "balance-2", productId: "product-2", sku: "WF-HINGE-200", upc: "724880002000", name: "Door hinge" }],
        nextCursor: null
      });

    render(
      <InventoryPage
        onChanged={vi.fn()}
        refreshVersion={0}
        role="staff"
        warehouse={{ id: "reno-id", name: "Reno" }}
      />
    );

    await screen.findByText("WF-LOCK-114");
    await user.click(screen.getByRole("button", { name: "Load more inventory" }));

    await waitFor(() => expect(wmsApi.getInventoryPage).toHaveBeenLastCalledWith("reno-id", { cursor: "inventory-next" }));
    expect(screen.getByText("WF-LOCK-114")).toBeInTheDocument();
    expect(await screen.findByText("WF-HINGE-200")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more inventory" })).not.toBeInTheDocument();
  });
});
