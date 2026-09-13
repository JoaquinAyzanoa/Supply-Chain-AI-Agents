import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type AutonomyPolicy = Schemas["AutonomyPolicy"];
export type AutonomyRule = Schemas["AutonomyRule"];
export type RuleConditions = Schemas["RuleConditions"];
export type PolicyView = Schemas["PolicyView"];
export type PreviewResponse = Schemas["PreviewResponse"];
export type PolicyUpdateResponse = Schemas["PolicyUpdateResponse"];
export type AutoAction = Schemas["AutoActionView"];

export const RULE_KINDS = ["*", "send_email", "po_change", "vendor_bill", "orderpoint_change", "planning_run", "supplier_score", "unlinked_mail"] as const;
export const LEVELS = ["approve", "auto_notice", "auto"] as const;
export const EMAIL_KINDS = ["rfq", "send_po", "follow_up", "request_eta", "reply", "discrepancy"] as const;

export function usePolicy() {
  return useQuery({ queryKey: ["autonomy", "policy"], queryFn: async () => unwrap(await api.GET("/api/autonomy")) });
}

export function useAutoActions(days: number) {
  return useQuery({
    queryKey: ["autonomy", "actions", days],
    queryFn: async () => unwrap(await api.GET("/api/autonomy/actions", { params: { query: { days } } })),
    refetchInterval: 60_000,
  });
}

export function usePreview() {
  return useMutation({
    mutationFn: async (input: { policy: AutonomyPolicy; days: number }) => unwrap(await api.POST("/api/autonomy/preview", { body: input })),
  });
}

export function useSavePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { policy: AutonomyPolicy; note: string | null }) => unwrap(await api.PUT("/api/autonomy", { body: input })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["autonomy"] });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

export function useRevertAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (actionId: number) =>
      unwrap(await api.POST("/api/autonomy/actions/{action_id}/revert", { params: { path: { action_id: actionId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["autonomy", "actions"] }),
  });
}
