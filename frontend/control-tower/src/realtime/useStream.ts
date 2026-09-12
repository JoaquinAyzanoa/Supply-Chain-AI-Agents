/**
 * Live updates: one EventSource on GET /api/stream for the whole app.
 * Every server event invalidates the TanStack Query keys it concerns, so
 * screens refetch instead of polling. The browser reconnects on its own
 * after network errors; after a hard close (an expired token, a proxy
 * timeout) we reopen with a small backoff.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

export const STREAM_EVENTS = [
  "case_updated",
  "approval_created",
  "approval_resolved",
  "run_finished",
  "settings_changed",
] as const;
export type StreamEvent = (typeof STREAM_EVENTS)[number];

/** Which query families each server event makes stale. */
export const INVALIDATIONS: Record<StreamEvent, string[]> = {
  case_updated: ["cases", "exceptions"],
  approval_created: ["approvals", "cases", "exceptions"],
  approval_resolved: ["approvals", "cases", "exceptions", "planning"],
  run_finished: ["runs", "cases", "planning"],
  settings_changed: ["settings"],
};

export function invalidateFor(queryClient: QueryClient, event: StreamEvent): void {
  for (const key of INVALIDATIONS[event]) void queryClient.invalidateQueries({ queryKey: [key] });
}

export type StreamStatus = "idle" | "open" | "reconnecting";

export function useStream(token: string | null): StreamStatus {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>("idle");

  useEffect(() => {
    if (!token || typeof EventSource === "undefined") {
      setStatus("idle");
      return;
    }
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let closed = false;

    const open = () => {
      source = new EventSource(`/api/stream?token=${encodeURIComponent(token)}`);
      source.onopen = () => {
        attempts = 0;
        setStatus("open");
      };
      for (const event of STREAM_EVENTS) {
        source.addEventListener(event, () => invalidateFor(queryClient, event));
      }
      source.onerror = () => {
        setStatus("reconnecting");
        if (source?.readyState === EventSource.CLOSED && !closed) {
          source.close();
          attempts += 1;
          retry = setTimeout(open, Math.min(30_000, 1_000 * 2 ** attempts));
        }
      };
    };
    open();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, [token, queryClient]);

  return status;
}
