import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api/client";
import type { AsyncStatus } from "../types/wms";

export interface ApiResource<T> {
  data: T | null;
  error: ApiError | null;
  reload: () => void;
  status: AsyncStatus;
}

export function useApiResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
  enabled = true
): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [status, setStatus] = useState<AsyncStatus>(enabled ? "loading" : "idle");
  const [reloadVersion, setReloadVersion] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(() => setReloadVersion((version) => version + 1), []);

  useEffect(() => {
    if (!enabled) {
      setStatus("idle");
      return;
    }
    const controller = new AbortController();
    setStatus("loading");
    setError(null);
    setData(null);
    loaderRef.current(controller.signal)
      .then((nextData) => {
        if (controller.signal.aborted) return;
        setData(nextData);
        setStatus("success");
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught instanceof ApiError ? caught : new ApiError("The WMS request failed.", 0));
        setStatus("error");
      });
    return () => controller.abort();
    // Dependencies are explicitly supplied by each feature boundary.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, enabled, reloadVersion]);

  return { data, error, reload, status };
}
