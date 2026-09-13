import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/api/client";

export type Suggestion = Schemas["Suggestion"];
export type FeedbackSummary = Schemas["FeedbackSummary"];
export type FeedbackStats = Schemas["FeedbackStats"];
export type SupplierProfile = Schemas["SupplierProfile"];
export type ProfileUpdate = Schemas["ProfileUpdate"];

export function useSuggestions() {
  return useQuery({ queryKey: ["learning", "suggestions"], queryFn: async () => unwrap(await api.GET("/api/learning/suggestions")) });
}

export function useFeedbackStats(days: number) {
  return useQuery({
    queryKey: ["learning", "stats", days],
    queryFn: async () => unwrap(await api.GET("/api/learning/stats", { params: { query: { days } } })),
  });
}

export function useSuggestionAction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; action: "accept" | "dismiss" }) =>
      input.action === "accept"
        ? unwrap(await api.POST("/api/learning/suggestions/{suggestion_id}/accept", { params: { path: { suggestion_id: input.id } } }))
        : unwrap(await api.POST("/api/learning/suggestions/{suggestion_id}/dismiss", { params: { path: { suggestion_id: input.id } } })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["learning"] });
      void queryClient.invalidateQueries({ queryKey: ["autonomy"] });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}

export function useSupplierProfile(partnerId: number | null) {
  return useQuery({
    queryKey: ["learning", "profile", partnerId],
    enabled: partnerId !== null,
    queryFn: async () => unwrap(await api.GET("/api/learning/profiles/{partner_id}", { params: { path: { partner_id: partnerId! } } })),
  });
}

export function useSaveProfile(partnerId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ProfileUpdate) => unwrap(await api.PUT("/api/learning/profiles/{partner_id}", { params: { path: { partner_id: partnerId } }, body })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["learning", "profile", partnerId] }),
  });
}
