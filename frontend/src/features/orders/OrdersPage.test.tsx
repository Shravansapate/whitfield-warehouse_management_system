import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { OrderRow } from "../../types/wms";
import { OrdersPage } from "./OrdersPage";

function pickingOrder(id: string, warehouseId: string, pickedQuantity = 0): OrderRow {
  return {
    id,
    displayId: id === "reno-order" ? "ORD-RENO" : "ORD-COLUMBUS",
    reference: `WEB-${id}`,
    status: "picking",
    itemCount: 1,
    units: 2,
    packageState: "Awaiting next step",
    warehouseId,
    items: [{
      id: `${id}-item`,
      productId: "product-1",
      sku: "WF-LOCK-114",
      name: "Smart deadbolt",
      quantity: 2,
      pickedQuantity
    }]
  };
}

describe("OrdersPage picking safety", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getInventoryPage").mockResolvedValue({ items: [], nextCursor: null });
    vi.spyOn(wmsApi, "packOrder").mockResolvedValue({});
    vi.spyOn(wmsApi, "confirmPickedItem").mockResolvedValue({});
    vi.spyOn(wmsApi, "cancelOrder").mockResolvedValue({});
  });

  it("resets package measurements and removes stale orders when the warehouse changes", async () => {
    vi.spyOn(wmsApi, "getOrdersPage").mockImplementation(async (warehouseId) => ({
      items: warehouseId === "reno-id"
        ? [pickingOrder("reno-order", "reno-id", 2)]
        : [pickingOrder("reno-order", "reno-id", 2), pickingOrder("columbus-order", "columbus-id", 2)],
      nextCursor: null
    }));
    const user = userEvent.setup();
    const { rerender } = render(<OrdersPage onChanged={vi.fn()} refreshVersion={0} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await screen.findByText(/ORD-RENO · picking/);
    await user.type(screen.getByLabelText("Weight lb"), "5.5");
    await user.type(screen.getByLabelText("Length in"), "10");

    rerender(<OrdersPage onChanged={vi.fn()} refreshVersion={0} warehouse={{ id: "columbus-id", name: "Columbus" }} />);

    await screen.findByText(/ORD-COLUMBUS · picking/);
    expect(screen.queryByText("ORD-RENO")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Weight lb")).toHaveValue(null);
    expect(screen.getByLabelText("Length in")).toHaveValue(null);
  });

  it("confirms every picked line before enabling packing", async () => {
    let picked = false;
    vi.spyOn(wmsApi, "getOrdersPage").mockImplementation(async () => ({ items: [pickingOrder("reno-order", "reno-id", picked ? 2 : 0)], nextCursor: null }));
    vi.mocked(wmsApi.confirmPickedItem).mockImplementation(async () => {
      picked = true;
      return {};
    });
    const user = userEvent.setup();
    render(<OrdersPage onChanged={vi.fn()} refreshVersion={0} warehouse={{ id: "reno-id", name: "Reno" }} />);

    expect(await screen.findByRole("button", { name: "Confirm packed" })).toBeDisabled();
    expect(screen.getByLabelText("Picking checklist")).toHaveTextContent("0 picked / 2 ordered");
    await user.click(screen.getByRole("button", { name: "Confirm 2 picked" }));

    await waitFor(() => expect(wmsApi.confirmPickedItem).toHaveBeenCalledWith(
      "reno-order",
      "reno-order-item",
      2,
      expect.stringMatching(/^order-item-pick-/)
    ));
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm packed" })).toBeEnabled());
    expect(screen.getByLabelText("Picking checklist")).toHaveTextContent("2 picked / 2 ordered");
  });

  it("refreshes orders and released inventory after a confirmed cancellation", async () => {
    const getOrders = vi.spyOn(wmsApi, "getOrdersPage").mockResolvedValue({ items: [pickingOrder("reno-order", "reno-id")], nextCursor: null });
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("Customer requested cancellation");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onChanged = vi.fn();
    const user = userEvent.setup();
    render(<OrdersPage onChanged={onChanged} refreshVersion={0} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.click(await screen.findByRole("button", { name: "Cancel order" }));

    expect(prompt).toHaveBeenCalledWith("Why is ORD-RENO being cancelled?");
    expect(confirm).toHaveBeenCalledWith("Cancel this order and release its active reservations?");
    await waitFor(() => expect(wmsApi.cancelOrder).toHaveBeenCalledWith(
      "reno-order",
      "Customer requested cancellation",
      expect.stringMatching(/^order-cancel-reno-order-/)
    ));
    await waitFor(() => {
      expect(getOrders).toHaveBeenCalledTimes(2);
      expect(wmsApi.getInventoryPage).toHaveBeenCalledTimes(2);
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole("status")).toHaveTextContent("active reservations were released");
  });

  it("loads more orders, deduplicates overlap, and removes the exhausted continuation", async () => {
    const user = userEvent.setup();
    const first = pickingOrder("reno-order", "reno-id");
    const older = { ...pickingOrder("older-order", "reno-id"), displayId: "ORD-OLDER", reference: "WEB-OLDER" };
    vi.spyOn(wmsApi, "getOrdersPage")
      .mockResolvedValueOnce({ items: [first], nextCursor: "orders-next" })
      .mockResolvedValueOnce({ items: [first, older], nextCursor: null });

    render(<OrdersPage onChanged={vi.fn()} refreshVersion={0} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await screen.findByRole("button", { name: /ORD-RENO/ });
    await user.click(screen.getByRole("button", { name: "Load more orders" }));

    await waitFor(() => expect(wmsApi.getOrdersPage).toHaveBeenLastCalledWith("reno-id", { cursor: "orders-next" }));
    expect(await screen.findByText("ORD-OLDER")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /ORD-RENO/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Load more orders" })).not.toBeInTheDocument();
  });
});
