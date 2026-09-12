/**
 * The board's data and the rules of the drag. Which column a card may be
 * dropped on is decided here, from the order's state, so the UI never offers
 * a move that Odoo would refuse (the API checks again).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type Board = Schemas["Board"];
export type BoardCard = Schemas["BoardCard"];
export type Column = BoardCard["column"];

export const COLUMNS: Column[] = ["proposed", "rfq_sent", "quote_received", "confirmed", "incoming", "received", "closed"];

export function useBoard() {
  return useQuery({
    queryKey: ["board"],
    queryFn: async () => unwrap(await api.GET("/api/board")),
    refetchInterval: 60_000,
  });
}

/** Where a card may be dragged: only the moves that are real actions in Odoo. */
export function targetsFor(card: BoardCard): Column[] {
  switch (card.column) {
    case "proposed":
      return ["rfq_sent", "confirmed", "closed"];
    case "rfq_sent":
    case "quote_received":
      return ["confirmed", "closed"];
    case "received":
      return ["closed"];
    default:
      return [];
  }
}

/** A card the buyer should look at before the rest. */
export function hasProblem(card: BoardCard): boolean {
  return card.delivery === "late" || card.pending_approval !== null || card.escalated || card.case_status === "failed";
}

function useRefreshAfterMove() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["board"] });
    void queryClient.invalidateQueries({ queryKey: ["cases"] });
    void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    void queryClient.invalidateQueries({ queryKey: ["exceptions"] });
  };
}

export function useMoveCard() {
  const refresh = useRefreshAfterMove();
  return useMutation({
    mutationFn: async ({ po_name, to, note }: { po_name: string; to: Column; note?: string }) =>
      unwrap(await api.POST("/api/board/{po_name}/move", { params: { path: { po_name } }, body: { to, note: note || null } })),
    onSettled: refresh,
  });
}

export function useSupplierConfirmed() {
  const refresh = useRefreshAfterMove();
  return useMutation({
    mutationFn: async ({ po_name, value }: { po_name: string; value: boolean }) =>
      unwrap(await api.POST("/api/board/{po_name}/supplier-confirmed", { params: { path: { po_name } }, body: { value } })),
    onSettled: refresh,
  });
}
