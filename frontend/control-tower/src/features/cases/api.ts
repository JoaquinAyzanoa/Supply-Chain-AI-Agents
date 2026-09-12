import { useQuery } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type CaseView = Schemas["CaseView"];
export type CaseDetail = Schemas["CaseDetail"];
export type CaseEvent = Schemas["CaseEventView"];
export type AgentRun = Schemas["AgentRunView"];

export interface CaseFilters {
  status?: string;
  kind?: string;
  po?: string;
  /** "7", "30" or "all" (days back); the screen defaults to 7. */
  days?: string;
}

export const DAY_RANGES = ["7", "30", "all"] as const;

/** The ISO day `days` days ago, or undefined for "all". */
export function sinceFor(days: string | undefined): string | undefined {
  const n = Number(days ?? "7");
  if (!Number.isFinite(n) || n <= 0) return undefined;
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

export const CASE_STATUSES = ["open", "awaiting_approval", "escalated", "done", "rejected", "failed"] as const;
export const CASE_KINDS = ["rfq", "eta", "inbound", "unlinked", "receipt", "planning", "invoice"] as const;

export function useCases(filters: CaseFilters) {
  return useQuery({
    queryKey: ["cases", "list", filters],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/cases", {
          params: {
            query: {
              status: (filters.status || undefined) as never,
              kind: (filters.kind || undefined) as never,
              po: filters.po || undefined,
              since: sinceFor(filters.days),
              limit: 200,
            },
          },
        }),
      ),
  });
}

export function useCase(caseId: string) {
  return useQuery({
    queryKey: ["cases", "one", caseId],
    queryFn: async () => unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: caseId } } })),
  });
}
