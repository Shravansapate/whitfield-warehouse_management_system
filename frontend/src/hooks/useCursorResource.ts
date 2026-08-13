import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api/client";
import type { CursorPage } from "../lib/api/client";
import type { AsyncStatus } from "../types/wms";

export interface CursorResource<T> {
  data: T[] | null;
  error: ApiError | null;
  loadMore: () => Promise<void>;
  loadMoreError: ApiError | null;
  loadingMore: boolean;
  nextCursor: string | null;
  reload: () => void;
  status: AsyncStatus;
}

function requestError(caught: unknown) {
  return caught instanceof ApiError ? caught : new ApiError("The WMS request failed.", 0);
}

function appendUnique<T extends { id: string }>(current: T[] | null, incoming: T[]) {
  const merged = [...(current ?? [])];
  const positions = new Map(merged.map((item, index) => [item.id, index]));
  for (const item of incoming) {
    const position = positions.get(item.id);
    if (position === undefined) {
      positions.set(item.id, merged.length);
      merged.push(item);
    } else {
      merged[position] = item;
    }
  }
  return merged;
}

export function useCursorResource<T extends { id: string }>(
  loader: (cursor?: string) => Promise<CursorPage<T>>,
  dependencies: readonly unknown[],
  enabled = true
): CursorResource<T> {
  const [data, setData] = useState<T[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<ApiError | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<AsyncStatus>(enabled ? "loading" : "idle");
  const [reloadVersion, setReloadVersion] = useState(0);
  const generation = useRef(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(() => setReloadVersion((version) => version + 1), []);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    setLoadMoreError(null);
    setLoadingMore(false);
    setNextCursor(null);
    setData(null);
    if (!enabled) {
      setError(null);
      setStatus("idle");
      return;
    }
    setError(null);
    setStatus("loading");
    loaderRef.current()
      .then((page) => {
        if (generation.current !== requestGeneration) return;
        setData(page.items);
        setNextCursor(page.nextCursor);
        setStatus("success");
      })
      .catch((caught: unknown) => {
        if (generation.current !== requestGeneration) return;
        setError(requestError(caught));
        setStatus("error");
      });
    return () => {
      if (generation.current === requestGeneration) generation.current += 1;
    };
    // Dependencies are explicitly supplied by each feature boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, enabled, reloadVersion]);

  const loadMore = useCallback(async () => {
    if (!enabled || !nextCursor || loadingMore) return;
    const requestGeneration = generation.current;
    const cursor = nextCursor;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const page = await loaderRef.current(cursor);
      if (generation.current !== requestGeneration) return;
      setData((current) => appendUnique(current, page.items));
      setNextCursor(page.nextCursor);
    } catch (caught) {
      if (generation.current !== requestGeneration) return;
      setLoadMoreError(requestError(caught));
    } finally {
      if (generation.current === requestGeneration) setLoadingMore(false);
    }
  }, [enabled, loadingMore, nextCursor]);

  return { data, error, loadMore, loadMoreError, loadingMore, nextCursor, reload, status };
}
