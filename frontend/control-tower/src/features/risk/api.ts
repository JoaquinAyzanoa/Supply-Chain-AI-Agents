import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";

export interface ProductRisk {
  product_id: number;
  product_ref: string;
  product_name: string;
  category?: string | null;
  on_hand: number;
  reserved: number;
  position: number;
  incoming_30: number;
  incoming_60: number;
  daily_mean: number;
  daily_sigma: number;
  forecast_method: string;
  days_of_cover?: number | null;
  p_stockout_30: number;
  p_stockout_60: number;
  open_po_names: string[];
  late_po_names: string[];
  suggested_qty: number;
  exposure: number;
  unit_price?: number | null;
}

export interface SupplierRisk {
  partner_id: number;
  partner_name: string;
  open_lines: number;
  overdue_lines: number;
  expected_late_lines: number;
  otif?: number | null;
  lead_time_sigma_days?: number | null;
  exposure: number;
}

export interface RiskReport {
  as_of: string;
  warehouse_code: string;
  products: ProductRisk[];
  suppliers: SupplierRisk[];
  cash_exposure: number;
  at_risk_30: number;
}

export interface DispatchResult {
  case_id: string;
  case_code?: string | null;
  thread_id: string;
  status: string;
  summary: string;
  approval_id?: number | null;
}

export function useRiskReport() {
  return useQuery({
    queryKey: ["risk", "report"],
    queryFn: async () => unwrap(await api.GET("/api/risk")) as unknown as RiskReport,
    staleTime: 60_000,
  });
}

export function useActOnRisk() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { product_id: number; qty?: number | null; po_name?: string | null }) =>
      unwrap(
        await api.POST("/api/risk/{product_id}/act", {
          params: { path: { product_id: input.product_id } },
          body: { qty: input.qty ?? null, po_name: input.po_name ?? null },
        }),
      ) as unknown as DispatchResult,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sourcing"] });
      void queryClient.invalidateQueries({ queryKey: ["board"] });
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}
