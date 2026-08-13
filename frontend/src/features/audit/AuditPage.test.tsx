import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { wmsApi } from "../../lib/api/client";
import type { AuditRow } from "../../types/wms";
import { AuditPage } from "./AuditPage";

const firstEvent: AuditRow = {
  id: "audit-1",
  actor: "Maya Manager",
  action: "order shipped",
  source: "web",
  target: "order-1",
  time: "2026-08-13T12:00:00Z"
};

describe("AuditPage cursor history", () => {
  it("loads older events, deduplicates overlap, and removes the exhausted continuation", async () => {
    const user = userEvent.setup();
    const olderEvent: AuditRow = {
      id: "audit-2",
      actor: "System",
      action: "opening balance posted",
      source: "system",
      target: "balance-1",
      time: "2026-08-01T12:00:00Z"
    };
    vi.spyOn(wmsApi, "getAuditLogsPage")
      .mockResolvedValueOnce({ items: [firstEvent], nextCursor: "audit-next" })
      .mockResolvedValueOnce({ items: [firstEvent, olderEvent], nextCursor: null });

    render(<AuditPage refreshVersion={0} warehouse={{ id: "reno-id", name: "Reno" }} />);

    await screen.findByText("order shipped");
    await user.click(screen.getByRole("button", { name: "Load more audit events" }));

    await waitFor(() => expect(wmsApi.getAuditLogsPage).toHaveBeenLastCalledWith("reno-id", { cursor: "audit-next" }));
    expect(await screen.findByText("opening balance posted")).toBeInTheDocument();
    expect(screen.getAllByText("order shipped")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Load more audit events" })).not.toBeInTheDocument();
  });
});
