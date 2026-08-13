import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api/client";
import { useCursorResource } from "./useCursorResource";

interface Row {
  id: string;
  value: string;
}

describe("useCursorResource", () => {
  it("appends subsequent pages, updates duplicates, and stops at the final cursor", async () => {
    const loader = vi.fn()
      .mockResolvedValueOnce({ items: [{ id: "one", value: "first" }], nextCursor: "next-page" })
      .mockResolvedValueOnce({ items: [{ id: "one", value: "updated" }, { id: "two", value: "second" }], nextCursor: null });
    const { result } = renderHook(() => useCursorResource<Row>(loader, []));

    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(result.current.nextCursor).toBe("next-page");

    await act(() => result.current.loadMore());

    expect(loader).toHaveBeenNthCalledWith(2, "next-page");
    expect(result.current.data).toEqual([
      { id: "one", value: "updated" },
      { id: "two", value: "second" }
    ]);
    expect(result.current.nextCursor).toBeNull();
  });

  it("retains loaded rows and exposes a retryable continuation error", async () => {
    const error = new ApiError("The next page failed.", 503, "PAGE_UNAVAILABLE");
    const loader = vi.fn()
      .mockResolvedValueOnce({ items: [{ id: "one", value: "first" }], nextCursor: "next-page" })
      .mockRejectedValueOnce(error);
    const { result } = renderHook(() => useCursorResource<Row>(loader, []));

    await waitFor(() => expect(result.current.status).toBe("success"));
    await act(() => result.current.loadMore());

    expect(result.current.data).toEqual([{ id: "one", value: "first" }]);
    expect(result.current.loadMoreError).toBe(error);
    expect(result.current.nextCursor).toBe("next-page");
  });
});
