import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type PlaybookView = Schemas["PlaybookView"];
export type StepView = Schemas["StepView"];
export type PlaybookRun = Schemas["PlaybookRun"];
export type PlaybookPosition = Schemas["PlaybookPosition"];
export type RunView = Schemas["RunView"];
export type StepRecord = Schemas["StepRecord"];

export function usePlaybooks() {
  return useQuery({ queryKey: ["playbooks", "list"], queryFn: async () => unwrap(await api.GET("/api/playbooks")) });
}

export function usePlaybookRuns(active: boolean) {
  return useQuery({
    queryKey: ["playbooks", "runs", active],
    queryFn: async () => unwrap(await api.GET("/api/playbooks/runs", { params: { query: { active, limit: 100 } } })),
    refetchInterval: 60_000,
  });
}

export function usePlaybookRun(runId: number | null) {
  return useQuery({
    queryKey: ["playbooks", "run", runId],
    queryFn: async () => unwrap(await api.GET("/api/playbooks/runs/{run_id}", { params: { path: { run_id: runId ?? 0 } } })),
    enabled: runId !== null,
  });
}

function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["playbooks"] });
  void queryClient.invalidateQueries({ queryKey: ["board"] });
  void queryClient.invalidateQueries({ queryKey: ["cases"] });
  void queryClient.invalidateQueries({ queryKey: ["approvals"] });
}

export function useStartPlaybook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { playbook: string; po_name: string; partner_id: number | null }) =>
      unwrap(await api.POST("/api/playbooks/runs", { body: input })),
    onSuccess: () => invalidateAll(queryClient),
  });
}

export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (runId: number) => unwrap(await api.POST("/api/playbooks/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSuccess: () => invalidateAll(queryClient),
  });
}

export function useTickPlaybooks() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/playbooks/tick")),
    onSuccess: () => invalidateAll(queryClient),
  });
}
