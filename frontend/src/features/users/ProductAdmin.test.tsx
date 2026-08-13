import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { InventoryRow, ProductRecord } from "../../types/wms";
import { ProductAdmin } from "./ProductAdmin";

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

const product: ProductRecord = {
  id: "product-1",
  sku: "WF-LOCK-114",
  upc: "724880001140",
  name: "Smart deadbolt",
  description: "Wi-Fi enabled deadbolt",
  isActive: true
};

describe("ProductAdmin", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getInventoryPage").mockResolvedValue({ items: [inventory], nextCursor: null });
    vi.spyOn(wmsApi, "getProduct").mockResolvedValue(product);
    vi.spyOn(wmsApi, "createProduct").mockResolvedValue({ ...product, id: "product-2", sku: "WF-SENSOR-200", upc: "724880002000", name: "Window sensor" });
    vi.spyOn(wmsApi, "updateProduct").mockResolvedValue({ ...product, name: "Smart deadbolt Pro" });
    vi.spyOn(wmsApi, "setProductThreshold").mockResolvedValue({ warehouse_id: "reno-id", product_id: "product-1", low_stock_threshold: 8 });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("creates a product and saves the selected warehouse threshold", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<ProductAdmin onChanged={onChanged} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await screen.findByText("WF-LOCK-114");
    fireEvent.change(screen.getByLabelText("Threshold for WF-LOCK-114"), { target: { value: "8" } });
    await user.click(screen.getByRole("button", { name: "Save threshold for WF-LOCK-114" }));
    await waitFor(() => expect(wmsApi.setProductThreshold).toHaveBeenCalledWith("reno-id", "product-1", 8));

    await user.click(screen.getByRole("button", { name: "Add product" }));
    await user.type(screen.getByLabelText("New product SKU"), "WF-SENSOR-200");
    await user.type(screen.getByLabelText("New product UPC"), "724880002000");
    await user.type(screen.getByLabelText("New product name"), "Window sensor");
    await user.type(screen.getByLabelText("New product description"), "Wireless contact sensor");
    await user.click(screen.getByRole("button", { name: "Create product" }));

    await waitFor(() => expect(wmsApi.createProduct).toHaveBeenCalledWith({
      sku: "WF-SENSOR-200",
      upc: "724880002000",
      name: "Window sensor",
      description: "Wireless contact sensor"
    }));
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("edits and deactivates an existing product while retaining history", async () => {
    const user = userEvent.setup();
    vi.mocked(wmsApi.updateProduct)
      .mockResolvedValueOnce({ ...product, name: "Smart deadbolt Pro" })
      .mockResolvedValueOnce({ ...product, name: "Smart deadbolt Pro", isActive: false });
    render(<ProductAdmin onChanged={vi.fn()} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await user.click(await screen.findByRole("button", { name: "Edit WF-LOCK-114" }));
    await screen.findByDisplayValue("Wi-Fi enabled deadbolt");
    await user.clear(screen.getByLabelText("Edit product name"));
    await user.type(screen.getByLabelText("Edit product name"), "Smart deadbolt Pro");
    await user.click(screen.getByRole("button", { name: "Save product" }));
    await waitFor(() => expect(wmsApi.updateProduct).toHaveBeenCalledWith("product-1", expect.objectContaining({ name: "Smart deadbolt Pro" })));

    await user.click(screen.getByRole("button", { name: "Deactivate product" }));
    await waitFor(() => expect(wmsApi.updateProduct).toHaveBeenLastCalledWith("product-1", { is_active: false }));
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });
});
