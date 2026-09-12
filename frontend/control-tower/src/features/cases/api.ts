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
