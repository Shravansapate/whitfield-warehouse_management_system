import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../lib/api/client";
import { DataState } from "./DataState";

describe("DataState", () => {
  it("renders loading and empty states without stale content", () => {
    const { rerender } = render(
      <DataState error={null} loading onRetry={vi.fn()}><span>stale rows</span></DataState>
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading live warehouse data");
    expect(screen.queryByText("stale rows")).not.toBeInTheDocument();

    rerender(
      <DataState dataLength={0} emptyMessage="Nothing in this scope." error={null} loading={false} onRetry={vi.fn()}><span>stale rows</span></DataState>
    );
    expect(screen.getByRole("status")).toHaveTextContent("Nothing in this scope.");
    expect(screen.queryByText("stale rows")).not.toBeInTheDocument();
  });

  it("explains authorization errors and retries explicitly", async () => {
    const retry = vi.fn();
    const user = userEvent.setup();
    render(
      <DataState error={new ApiError("Cross-warehouse access is not allowed", 403, "WAREHOUSE_FORBIDDEN", "request-1")} loading={false} onRetry={retry}>
        <span>protected rows</span>
      </DataState>
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Access restricted");
    expect(alert).toHaveTextContent("Request ID: request-1");
    expect(screen.queryByText("protected rows")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
