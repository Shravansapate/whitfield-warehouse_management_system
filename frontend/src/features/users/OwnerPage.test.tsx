import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { TeamMember, WarehouseRef } from "../../types/wms";
import { OwnerPage } from "./OwnerPage";

const warehouses: WarehouseRef[] = [
  { id: "reno-id", name: "Reno" },
  { id: "columbus-id", name: "Columbus" }
];

const member: TeamMember = {
  id: "user-1",
  name: "Avery Manager",
  email: "avery@example.com",
  role: "manager",
  warehouse: "Reno",
  warehouseId: "reno-id",
  state: "Active"
};

describe("OwnerPage user access", () => {
  beforeEach(() => {
    vi.spyOn(wmsApi, "getUsersPage").mockResolvedValue({ items: [member], nextCursor: null });
    vi.spyOn(wmsApi, "getInventoryPage").mockResolvedValue({ items: [], nextCursor: null });
    vi.spyOn(wmsApi, "assignUserWarehouse").mockResolvedValue({ ...member, warehouse: "Columbus", warehouseId: "columbus-id" });
    vi.spyOn(wmsApi, "updateUser").mockResolvedValue(member);
    vi.spyOn(wmsApi, "resetUserPassword").mockResolvedValue({ detail: "Password reset and active sessions revoked" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("explicitly reassigns a non-owner to a selected warehouse", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn();
    render(<OwnerPage onChanged={onChanged} warehouse={warehouses[0]} warehouses={warehouses} />);

    await screen.findByText("Avery Manager");
    await user.selectOptions(screen.getByLabelText("Warehouse for Avery Manager"), "columbus-id");
    await user.click(screen.getByRole("button", { name: "Save access for Avery Manager" }));

    await waitFor(() => expect(wmsApi.assignUserWarehouse).toHaveBeenCalledWith("user-1", "columbus-id"));
    expect(wmsApi.updateUser).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it("changes role and warehouse atomically through one user update", async () => {
    const user = userEvent.setup();
    render(<OwnerPage onChanged={vi.fn()} warehouse={warehouses[0]} warehouses={warehouses} />);

    await screen.findByText("Avery Manager");
    await user.selectOptions(screen.getByLabelText("Role for Avery Manager"), "trusted");
    await user.selectOptions(screen.getByLabelText("Warehouse for Avery Manager"), "columbus-id");
    await user.click(screen.getByRole("button", { name: "Save access for Avery Manager" }));

    await waitFor(() => expect(wmsApi.updateUser).toHaveBeenCalledWith("user-1", {
      role: "trusted",
      warehouse_id: "columbus-id"
    }));
    expect(wmsApi.assignUserWarehouse).not.toHaveBeenCalled();
  });

  it("resets a password only after validation and confirmation", async () => {
    const user = userEvent.setup();
    render(<OwnerPage onChanged={vi.fn()} warehouse={warehouses[0]} warehouses={warehouses} />);

    await screen.findByText("Avery Manager");
    await user.click(screen.getByRole("button", { name: "Reset password for Avery Manager" }));
    await user.type(screen.getByLabelText("New password for Avery Manager"), "short");
    await user.click(screen.getByRole("button", { name: "Reset and revoke sessions" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 10 characters");
    expect(wmsApi.resetUserPassword).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText("New password for Avery Manager"));
    await user.type(screen.getByLabelText("New password for Avery Manager"), "New-Secure-Password!");
    await user.click(screen.getByRole("button", { name: "Reset and revoke sessions" }));

    await waitFor(() => expect(wmsApi.resetUserPassword).toHaveBeenCalledWith("user-1", "New-Secure-Password!"));
    expect(screen.getByText(/password was reset and active sessions were revoked/)).toBeInTheDocument();
  });

  it("loads older users from the next cursor page", async () => {
    const user = userEvent.setup();
    const olderMember: TeamMember = {
      ...member,
      id: "user-2",
      name: "Jordan Staff",
      email: "jordan@example.com",
      role: "staff"
    };
    vi.mocked(wmsApi.getUsersPage)
      .mockResolvedValueOnce({ items: [member], nextCursor: "users-next" })
      .mockResolvedValueOnce({ items: [olderMember], nextCursor: null });

    render(<OwnerPage onChanged={vi.fn()} warehouse={warehouses[0]} warehouses={warehouses} />);

    await screen.findByText("Avery Manager");
    await user.click(screen.getByRole("button", { name: "Load more users" }));

    await waitFor(() => expect(wmsApi.getUsersPage).toHaveBeenLastCalledWith({ cursor: "users-next" }));
    expect(screen.getByText("Avery Manager")).toBeInTheDocument();
    expect(await screen.findByText("Jordan Staff")).toBeInTheDocument();
  });
});
