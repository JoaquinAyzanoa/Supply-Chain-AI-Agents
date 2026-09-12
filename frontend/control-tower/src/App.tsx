import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { AuthProvider, useAuth } from "@/auth/AuthProvider";
import { I18nProvider } from "@/i18n";
import { createAppRouter } from "@/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: true },
  },
});

export function Routed() {
  const auth = useAuth();
  // One router for the app's life. A session change (sign in, sign out, an expired
  // token) re-runs the route guards so the right screen shows without a reload.
  const router = useMemo(() => createAppRouter({ auth, queryClient }), []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void router.invalidate();
  }, [router, auth.session]);
  return <RouterProvider router={router} context={{ auth, queryClient }} />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  );
}
