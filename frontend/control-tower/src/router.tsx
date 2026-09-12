/**
 * Code-based TanStack Router tree. `/login` is public; everything under the
 * app route needs a session (redirect to /login with the intended URL) and
 * `/settings` needs the admin role.
 */
import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";
import { z } from "zod";

import type { AuthState } from "@/auth/AuthProvider";
import { atLeast, authStore } from "@/auth/store";
import { AppShell } from "@/components/layout/AppShell";
import { LoginPage } from "@/routes/login";
import {
  ApprovalsPage,
  CaseDetailPage,
  CasesPage,
  ExceptionsPage,
  PlanningPage,
  PlanningRunPage,
  RunsPage,
  SettingsPage,
} from "@/routes/placeholders";

export interface RouterContext {
  auth: AuthState;
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({ component: Outlet });

const loginSearch = z.object({ redirect: z.string().optional() });

export const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (search) => loginSearch.parse(search),
  // Guards read the session store directly: it is always current, while the router
  // context is only as fresh as the last render.
  beforeLoad: ({ search }) => {
    if (authStore.get()) throw redirect({ to: search.redirect ?? "/" });
  },
  component: LoginPage,
});

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: ({ location }) => {
    if (!authStore.get()) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
  },
  component: AppShell,
});

const child = <P extends string>(path: P, component: () => React.ReactNode) =>
  createRoute({ getParentRoute: () => appRoute, path, component });

export const approvalsRoute = child("/", ApprovalsPage);
export const casesRoute = child("/cases", CasesPage);
export const caseRoute = child("/cases/$caseId", CaseDetailPage);
export const exceptionsRoute = child("/exceptions", ExceptionsPage);
export const planningRoute = child("/planning", PlanningPage);
export const planningRunRoute = child("/planning/$runId", PlanningRunPage);
export const runsRoute = child("/runs", RunsPage);
export const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/settings",
  beforeLoad: () => {
    if (!atLeast(authStore.get()?.user.role, "admin")) throw redirect({ to: "/" });
  },
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([
    approvalsRoute,
    casesRoute,
    caseRoute,
    exceptionsRoute,
    planningRoute,
    planningRunRoute,
    runsRoute,
    settingsRoute,
  ]),
]);

export function createAppRouter(context: RouterContext) {
  return createRouter({ routeTree, context, defaultPreload: "intent" });
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
