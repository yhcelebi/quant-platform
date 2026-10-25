"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type State<T> = { key: string; data: T | null; error: string | null };

/** GET with loading/error state; ``reload`` refetches. A null path skips the request. */
export function useApi<T>(path: string | null, params?: Record<string, string | undefined>) {
  const query = JSON.stringify(params ?? {});
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<State<T>>({ key: "", data: null, error: null });
  const key = path ? `${path}|${query}|${nonce}` : "";

  useEffect(() => {
    if (!path) return;
    let alive = true;
    api.get<T>(path, JSON.parse(query)).then(
      (data) => alive && setState({ key, data, error: null }),
      (e: unknown) =>
        alive &&
        setState((s) => ({ key, data: s.data, error: e instanceof Error ? e.message : String(e) })),
    );
    return () => {
      alive = false;
    };
  }, [key, path, query]);

  return {
    data: state.data,
    error: state.error,
    loading: Boolean(path) && state.key !== key,
    reload: () => setNonce((n) => n + 1),
  };
}
