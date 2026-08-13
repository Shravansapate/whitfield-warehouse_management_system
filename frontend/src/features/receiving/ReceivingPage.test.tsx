import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { ReceiptRow } from "../../types/wms";
import { ReceivingPage } from "./ReceivingPage";

const receipt: ReceiptRow = {
  id: "receipt-1",
  displayId: "INB-1048",
  sender: "West supplier",
  reference: "1Z123",
  status: "receiving",
  accepted: 1,
  damaged: 0,
  lines: 1,
  warehouseId: "reno-id",
  items: [{ id: "line-1", productId: "product-1", sku: "WF-LOCK-114", name: "Smart deadbolt", quantityReceived: 1, quantityAccepted: 1, quantityDamaged: 0 }]
};

const columbusReceipt: ReceiptRow = {
  ...receipt,
  id: "receipt-2",
  displayId: "INB-2048",
  reference: "CMH-2048",
  warehouseId: "columbus-id"
};

describe("ReceivingPage", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getReceiptsPage").mockResolvedValue({ items: [receipt], nextCursor: null });
    vi.spyOn(wmsApi, "getReceipt").mockResolvedValue(receipt);
    vi.spyOn(wmsApi, "getDamagedReturnsPage").mockResolvedValue({ items: [], nextCursor: null });
    vi.spyOn(wmsApi, "searchProductsPage").mockResolvedValue({
      items: [{ id: "product-1", sku: "WF-LOCK-114", upc: "724880001140", name: "Smart deadbolt", isActive: true }],
      nextCursor: null
    });
    vi.spyOn(wmsApi, "addReceiptItem").mockResolvedValue(receipt);
    vi.spyOn(wmsApi, "updateReceiptItem").mockResolvedValue(receipt);
    vi.spyOn(wmsApi, "deleteReceiptItem").mockResolvedValue(receipt);
    vi.spyOn(wmsApi, "receiveReceipt").mockResolvedValue({});
  });

  it("validates scanner totals, saves a draft line, and confirms finalization", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.type(await screen.findByLabelText("Scan product UPC"), "724880001140");
    await user.click(screen.getByRole("button", { name: "Find" }));
    await screen.findByText(/Selected WF-LOCK-114/);

    await user.clear(screen.getByLabelText("Received"));
    await user.type(screen.getByLabelText("Received"), "2");
    await user.click(screen.getByRole("button", { name: /save draft line/i }));
    expect(screen.getByRole("alert")).toHaveTextContent("accepted plus damaged equals");

    await user.clear(screen.getByLabelText("Damaged"));
    await user.type(screen.getByLabelText("Damaged"), "1");
    await user.type(screen.getByLabelText("Damage notes"), "Crushed corner");
    await user.click(screen.getByRole("button", { name: /save draft line/i }));

    await waitFor(() => expect(wmsApi.addReceiptItem).toHaveBeenCalledWith("receipt-1", expect.objectContaining({
      product_id: "product-1",
      quantity_received: 2,
      quantity_accepted: 1,
      quantity_damaged: 1
    }), expect.stringMatching(/^receipt-item-/)));

    await user.click(screen.getByRole("button", { name: /complete receiving/i }));
    await waitFor(() => expect(wmsApi.receiveReceipt).toHaveBeenCalledWith("receipt-1", expect.stringMatching(/^receipt-receive-/)));
    expect(confirm).toHaveBeenCalled();
  });

  it("corrects and deletes individual draft lines with validation and confirmation", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.click(await screen.findByRole("button", { name: "Edit WF-LOCK-114 draft line" }));
    await user.clear(screen.getByLabelText("Received for WF-LOCK-114"));
    await user.type(screen.getByLabelText("Received for WF-LOCK-114"), "3");
    await user.click(screen.getByRole("button", { name: "Save correction" }));
    expect(screen.getByRole("alert")).toHaveTextContent("accepted plus damaged equals");
    expect(wmsApi.updateReceiptItem).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("Accepted for WF-LOCK-114"));
    await user.type(screen.getByLabelText("Accepted for WF-LOCK-114"), "2");
    await user.clear(screen.getByLabelText("Damaged for WF-LOCK-114"));
    await user.type(screen.getByLabelText("Damaged for WF-LOCK-114"), "1");
    await user.type(screen.getByLabelText("Damage notes for WF-LOCK-114"), "Torn packaging");
    await user.click(screen.getByRole("button", { name: "Save correction" }));

    await waitFor(() => expect(wmsApi.updateReceiptItem).toHaveBeenCalledWith(
      "receipt-1",
      "line-1",
      {
        quantity_received: 3,
        quantity_accepted: 2,
        quantity_damaged: 1,
        damage_notes: "Torn packaging"
      },
      expect.stringMatching(/^receipt-item-update-line-1-/)
    ));
    expect(await screen.findByText(/draft line corrected/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete WF-LOCK-114 draft line" }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("no inventory has posted yet"));
    await waitFor(() => expect(wmsApi.deleteReceiptItem).toHaveBeenCalledWith(
      "receipt-1",
      "line-1",
      expect.stringMatching(/^receipt-item-delete-line-1-/)
    ));
    expect(await screen.findByText(/Inventory remains unchanged/)).toBeInTheDocument();
  });

  it("lets staff view damaged returns without exposing completion controls", async () => {
    vi.mocked(wmsApi.getDamagedReturnsPage).mockResolvedValue({
      items: [{
        id: "return-1",
        receiptId: "receipt-1",
        productName: "Smart deadbolt",
        quantity: 1,
        status: "pending_return"
      }],
      nextCursor: null
    });

    render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="staff" warehouse={{ id: "reno-id", name: "Reno" }} />);

    expect(await screen.findByText("Smart deadbolt · 1 units")).toBeInTheDocument();
    expect(screen.getByText("Trusted, manager, or owner access is required to close this return.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark returned" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Return tracking for Smart deadbolt")).not.toBeInTheDocument();
  });

  it("clears receipt and scan state and filters stale records when the warehouse changes", async () => {
    vi.mocked(wmsApi.getReceiptsPage).mockImplementation(async (warehouseId) => ({
      items: warehouseId === "reno-id" ? [receipt] : [receipt, columbusReceipt],
      nextCursor: null
    }));
    vi.mocked(wmsApi.getReceipt).mockImplementation(async (id) => id === receipt.id ? receipt : columbusReceipt);
    const user = userEvent.setup();
    const { rerender } = render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.type(await screen.findByLabelText("Scan product UPC"), "724880001140");
    await user.clear(screen.getByLabelText("Received"));
    await user.type(screen.getByLabelText("Received"), "7");
    await user.click(screen.getByRole("button", { name: "New receipt" }));
    await user.type(screen.getByLabelText("Sender name"), "Draft Reno sender");
    await user.click(screen.getByRole("button", { name: "Edit WF-LOCK-114 draft line" }));
    await user.clear(screen.getByLabelText("Received for WF-LOCK-114"));
    await user.type(screen.getByLabelText("Received for WF-LOCK-114"), "9");

    rerender(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "columbus-id", name: "Columbus" }} />);

    await screen.findAllByText("INB-2048");
    expect(screen.queryByText("INB-1048")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Sender name")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Received for WF-LOCK-114")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Scan product UPC")).toHaveValue("");
    expect(screen.getByLabelText("Received")).toHaveValue(1);
    await waitFor(() => expect(wmsApi.getReceipt).toHaveBeenCalledWith("receipt-2"));
  });

  it("loads more product search matches with the returned cursor", async () => {
    const user = userEvent.setup();
    vi.mocked(wmsApi.searchProductsPage)
      .mockResolvedValueOnce({
        items: [{ id: "product-1", sku: "WF-LOCK-114", upc: "724880001140", name: "Smart deadbolt", isActive: true }],
        nextCursor: "products-next"
      })
      .mockResolvedValueOnce({
        items: [{ id: "product-2", sku: "WF-LOCK-115", upc: "724880001150", name: "Keypad lock", isActive: true }],
        nextCursor: null
      });
    render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.type(await screen.findByLabelText("Scan product UPC"), "lock");
    await user.click(screen.getByRole("button", { name: "Find" }));
    await screen.findByText("Smart deadbolt");
    await user.click(screen.getByRole("button", { name: "Load more products" }));

    await waitFor(() => expect(wmsApi.searchProductsPage).toHaveBeenLastCalledWith("lock", { cursor: "products-next" }));
    expect(screen.getByText("Smart deadbolt")).toBeInTheDocument();
    expect(await screen.findByText("Keypad lock")).toBeInTheDocument();
  });

  it("loads more receipts and pending damaged returns without duplicate records", async () => {
    const user = userEvent.setup();
    const olderReceipt: ReceiptRow = { ...receipt, id: "receipt-older", displayId: "INB-0999", reference: "OLD-999", status: "received" };
    const firstReturn = { id: "return-1", receiptId: "receipt-1", productName: "Smart deadbolt", quantity: 1, status: "pending_return" as const };
    const olderReturn = { id: "return-2", receiptId: "receipt-older", productName: "Door hinge", quantity: 2, status: "pending_return" as const };
    vi.mocked(wmsApi.getReceiptsPage)
      .mockResolvedValueOnce({ items: [receipt], nextCursor: "receipts-next" })
      .mockResolvedValueOnce({ items: [receipt, olderReceipt], nextCursor: null });
    vi.mocked(wmsApi.getDamagedReturnsPage)
      .mockResolvedValueOnce({ items: [firstReturn], nextCursor: "returns-next" })
      .mockResolvedValueOnce({ items: [firstReturn, olderReturn], nextCursor: null });

    render(<ReceivingPage onChanged={vi.fn()} refreshVersion={0} role="trusted" warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.click(await screen.findByRole("button", { name: "Load more receipts" }));
    await waitFor(() => expect(wmsApi.getReceiptsPage).toHaveBeenLastCalledWith("reno-id", { cursor: "receipts-next" }));
    expect(await screen.findByText("INB-0999")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /INB-1048/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Load more receipts" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load more damaged returns" }));
    await waitFor(() => expect(wmsApi.getDamagedReturnsPage).toHaveBeenLastCalledWith("reno-id", { cursor: "returns-next", status: "pending_return" }));
    expect(await screen.findByText(/Door hinge.*2 units/)).toBeInTheDocument();
    expect(screen.getAllByText(/Smart deadbolt.*1 units/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Load more damaged returns" })).not.toBeInTheDocument();
  });
});
