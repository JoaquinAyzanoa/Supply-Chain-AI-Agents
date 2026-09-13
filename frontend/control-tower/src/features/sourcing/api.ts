import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";

/** A round as the sourcing agent lists it (the director passes it through). */
export interface RoundRfq {
  partner_id: number;
  partner_name: string;
  po_id?: number | null;
  po_name?: string | null;
  status: string;
  sent_at?: string | null;
  replied_at?: string | null;
}

export interface SourcingRound {
  id: number;
  case_id: string;
  status: string;
  source_po_name?: string | null;
  incumbent_partner_id?: number | null;
  basket: { product_id: number; product: string; qty: number; last_paid?: number | null; currency?: string | null }[];
  deadline: string;
  rfqs: RoundRfq[];
  award_approval_id?: number | null;
  awarded_partner_id?: number | null;
  awarded_po_name?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DispatchResult {
  case_id: string;
  case_code?: string | null;
  thread_id: string;
  status: string;
  summary: string;
  approval_id?: number | null;
}

export function useRounds(partnerId?: number | null, status?: string) {
  return useQuery({
    queryKey: ["sourcing", "rounds", partnerId ?? null, status ?? null],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/sourcing/rounds", {
          params: { query: { partner_id: partnerId ?? undefined, status: status ?? undefined } },
        }),
      ) as unknown as SourcingRound[],
    refetchInterval: 60_000,
  });
}

function invalidate(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["sourcing"] });
  void queryClient.invalidateQueries({ queryKey: ["board"] });
  void queryClient.invalidateQueries({ queryKey: ["approvals"] });
  void queryClient.invalidateQueries({ queryKey: ["cases"] });
}

export function useStartRound() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { po_name?: string | null; product_id?: number | null; qty?: number | null; partner_ids?: number[]; notes?: string | null }) =>
      unwrap(await api.POST("/api/sourcing/rounds", { body: input })) as unknown as DispatchResult,
    onSuccess: () => invalidate(queryClient),
  });
}

export function useCompareRound() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (roundId: number) =>
      unwrap(await api.POST("/api/sourcing/rounds/{round_id}/compare", { params: { path: { round_id: roundId } } })) as unknown as DispatchResult,
    onSuccess: () => invalidate(queryClient),
  });
}

export function useNegotiate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { po_name: string; product_id?: number | null; target_price?: number | null }) =>
      unwrap(await api.POST("/api/sourcing/negotiate", { body: input })) as unknown as DispatchResult,
    onSuccess: () => invalidate(queryClient),
  });
}
