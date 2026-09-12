import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";
import type { Approval } from "./types";

export type ListStatus = "pending" | "approved" | "rejected" | "expired" | "all";

export interface ApprovalFilters {
  status: ListStatus;
  kind?: string;
  po?: string;
}

export const approvalsKey = (filters: ApprovalFilters) => ["approvals", "list", filters] as const;

export function useApprovals(filters: ApprovalFilters) {
  return useQuery({
    queryKey: approvalsKey(filters),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/approvals", {
          params: { query: { status: filters.status, kind: filters.kind || undefined, po: filters.po || undefined } },
        }),
      ),
  });
}

export function useApproval(id: number | undefined) {
  return useQuery({
    queryKey: ["approvals", "one", id],
    enabled: id !== undefined,
    queryFn: async () =>
      unwrap(await api.GET("/api/approvals/{approval_id}", { params: { path: { approval_id: id! } } })),
  });
}

export type ResolveInput = { id: number } & Schemas["ResolveRequest"];

/**
 * Resolve an approval. The pending list drops the row at once (optimistic) and
 * every approvals query refetches when the server answers; the SSE
 * `approval_resolved` event does the same for other browsers.
 */
export function useResolveApproval() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: ResolveInput) =>
      unwrap(await api.POST("/api/approvals/{approval_id}/resolve", { params: { path: { approval_id: id } }, body })),
    onMutate: async ({ id }) => {
      await queryClient.cancelQueries({ queryKey: ["approvals", "list"] });
      const previous = queryClient.getQueriesData<Approval[]>({ queryKey: ["approvals", "list"] });
      queryClient.setQueriesData<Approval[]>({ queryKey: ["approvals", "list"] }, (rows) =>
        rows?.filter((row) => row.id !== id || row.status !== "pending"),
      );
      return { previous };
    },
    onError: (_error, _input, context) => {
      for (const [key, data] of context?.previous ?? []) queryClient.setQueryData(key, data);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}
