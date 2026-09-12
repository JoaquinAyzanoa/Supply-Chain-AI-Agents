import { useMutation, useQuery } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type PlanningRun = Schemas["PlanningRunRow"];
export type PlanningRunDetail = Schemas["PlanningRunDetail"];
export type PlanningLineRow = Schemas["PlanningLineRow"];
export type ReplenishmentLine = Schemas["ReplenishmentLine"];
export type PlanningOverrides = Schemas["PlanningOverrides"];
export type WhatIfResponse = Schemas["WhatIfResponse"];
export type LineDemand = Schemas["LineDemand"];

export const ACTIONABLE = new Set(["update_rule", "create_rfq", "update_rule_and_rfq"]);

export function usePlanningRuns() {
  return useQuery({
    queryKey: ["planning", "runs"],
    queryFn: async () => unwrap(await api.GET("/api/planning/runs", { params: { query: { limit: 30 } } })),
  });
}

export function usePlanningRun(runId: string) {
  return useQuery({
    queryKey: ["planning", "run", runId],
    queryFn: async () => unwrap(await api.GET("/api/planning/runs/{run_id}", { params: { path: { run_id: runId } } })),
  });
}

export function useLineDemand(runId: string, lineId: string | null) {
  return useQuery({
    queryKey: ["planning", "demand", runId, lineId],
    enabled: lineId !== null,
    staleTime: 5 * 60_000,
    queryFn: async () =>
      unwrap(
        await api.GET("/api/planning/runs/{run_id}/lines/{line_id}/demand", {
          params: { path: { run_id: runId, line_id: lineId! }, query: { days: 90 } },
        }),
      ),
  });
}

export function useWhatIf(runId: string) {
  return useMutation({
    mutationFn: async (input: { line_id: string; overrides: PlanningOverrides }) =>
      unwrap(await api.POST("/api/planning/runs/{run_id}/what-if", { params: { path: { run_id: runId } }, body: input })),
  });
}
