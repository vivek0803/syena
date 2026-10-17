import { useMutation, useQuery, useQueryClient, UseQueryOptions } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "./api";

export function useApi<T = any>(path: string | null, opts: Partial<UseQueryOptions<T>> = {}) {
  return useQuery<T>({ queryKey: [path], queryFn: () => api<T>(path as string), enabled: !!path, ...opts });
}

/** Mutation that refreshes the given query prefixes afterwards. */
export function useAct<TBody = any, TOut = any>(fn: (b: TBody) => Promise<TOut>, invalidate: string[] = []) {
  const qc = useQueryClient();
  return useMutation<TOut, Error, TBody>({
    mutationFn: fn,
    onSettled: () => invalidate.forEach((p) => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0] ?? "").startsWith(p) })),
  });
}

const streamTargets: Record<string, string[]> = {
  decision: ["/api/dashboard", "/api/decisions", "/api/trades", "/api/positions", "/api/orders"],
  trade_closed: ["/api/dashboard", "/api/trades", "/api/positions", "/api/learning"],
  alert: ["/api/dashboard"],
  news_cycle: ["/api/news", "/api/dashboard"],
  news_sell_signal: ["/api/dashboard"],
};

/** Live updates: server-sent events from /api/stream refresh the affected views. */
export function useLiveStream(enabled: boolean, onEvent?: (kind: string, data: any) => void) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      es = new EventSource("/api/stream");
      Object.keys(streamTargets).forEach((kind) =>
        es!.addEventListener(kind, (e) => {
          streamTargets[kind].forEach((p) => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0] ?? "").startsWith(p) }));
          try { onEvent?.(kind, JSON.parse((e as MessageEvent).data)); } catch { /* ignore */ }
        }),
      );
      es.onerror = () => { es?.close(); retry = setTimeout(connect, 5000); };
    };
    connect();
    return () => { es?.close(); clearTimeout(retry); };
  }, [enabled, qc, onEvent]);
}
