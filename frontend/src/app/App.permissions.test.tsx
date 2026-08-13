import { render, screen } from "@testing-library/react";
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
      id: "staff-1",
      name: "Ari Lane",
      email: "ari@example.com",
      role: "staff",
      isActive: true,
      warehouses: [{ id: "reno-id", code: "RNO", name: "Reno" }]
    }
  })
}));

describe("application permission navigation", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getWarehousesPage").mockResolvedValue({ items: [{ id: "reno-id", code: "RNO", name: "Reno" }], nextCursor: null });
    vi.spyOn(wmsApi, "getDashboard").mockResolvedValue({ availableUnits: 10, reservedUnits: 2, receivingBacklog: 1, ordersToShip: 1, damagedReturns: 0, auditEvents: 4 });
    vi.spyOn(wmsApi, "getLowStockPage").mockResolvedValue({ items: [], nextCursor: null });
  });

  it("keeps audit and owner controls out of a staff session", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("button", { name: "Receiving" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Audit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Owner" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Warehouse" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Open low-stock notifications" }));
    expect(screen.getByText("Low-stock alerts are shown in the selected warehouse command view.")).toBeInTheDocument();
  });
});
