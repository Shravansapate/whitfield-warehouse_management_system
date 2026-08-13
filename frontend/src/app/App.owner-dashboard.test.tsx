import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../lib/api/client";
import { App } from "./App";

vi.mock("../features/auth/AuthContext", () => ({
  useAuth: () => ({
    status: "authenticated",
    error: null,
    logout: vi.fn(),
    user: {
      id: "owner-1",
      name: "Whitfield Owner",
      email: "owner@example.com",
      role: "owner",
      isActive: true,
      warehouses: [
        { id: "reno-id", code: "RNO", name: "Reno" },
        { id: "columbus-id", code: "CMH", name: "Columbus" }
      ]
    }
  })
}));

describe("owner dashboard scope", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getWarehousesPage").mockResolvedValue({
      items: [
        { id: "reno-id", code: "RNO", name: "Reno" },
        { id: "columbus-id", code: "CMH", name: "Columbus" }
      ],
      nextCursor: null
    });
    vi.spyOn(wmsApi, "getDashboard").mockResolvedValue({ availableUnits: 10, reservedUnits: 2, receivingBacklog: 1, ordersToShip: 1, damagedReturns: 0, auditEvents: 4 });
    vi.spyOn(wmsApi, "getLowStockPage").mockResolvedValue({ items: [], nextCursor: null });
  });

  it("offers an explicit all-warehouse view and requests combined metrics", async () => {
    const user = userEvent.setup();
    render(<App />);

    const warehouse = await screen.findByRole("combobox", { name: "Warehouse" });
    expect(warehouse).toBeEnabled();
    expect(screen.getByRole("option", { name: "All warehouses" })).toBeInTheDocument();

    await user.selectOptions(warehouse, "all-warehouses");

    expect(await screen.findByText("All warehouses active", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Reno and Columbus can receive, reserve, pack, label, and audit/ })).toBeInTheDocument();
    await waitFor(() => expect(wmsApi.getDashboard).toHaveBeenLastCalledWith(undefined));
  });

  it("adds another authorized warehouse from the next cursor page", async () => {
    const user = userEvent.setup();
    vi.mocked(wmsApi.getWarehousesPage)
      .mockResolvedValueOnce({ items: [{ id: "reno-id", code: "RNO", name: "Reno" }], nextCursor: "warehouses-next" })
      .mockResolvedValueOnce({ items: [{ id: "columbus-id", code: "CMH", name: "Columbus" }], nextCursor: null });

    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Load more warehouses" }));

    await waitFor(() => expect(wmsApi.getWarehousesPage).toHaveBeenLastCalledWith({ cursor: "warehouses-next" }));
    expect(screen.getByRole("option", { name: "Reno" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Columbus" })).toBeInTheDocument();
  });
});
