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
import { ApprovalsInbox, type ApprovalsSearch } from "@/features/approvals/ApprovalsInbox";
import { BoardPage, type BoardSearch } from "@/features/board/BoardPage";
import { CaseTimelinePage } from "@/features/cases/CaseTimeline";
import { CasesPage } from "@/features/cases/CasesPage";
import { ExceptionsBoardPage } from "@/features/exceptions/ExceptionsBoard";
import { PlanningPage } from "@/features/planning/PlanningPage";
import { PlanningRunPage } from "@/features/planning/PlanningRunPage";
import { RunsPage, type RunsSearch } from "@/features/runs/RunsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";

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

const approvalsSearch = z.object({
  id: z.coerce.number().int().optional(),
  tab: z.enum(["pending", "resolved"]).optional(),
  kind: z.string().optional(),
  po: z.string().optional(),
});

export const approvalsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/approvals",
  validateSearch: (search): ApprovalsSearch => approvalsSearch.parse(search),
  component: ApprovalsInbox,
});
const boardSearch = z.object({
  po: z.string().optional(),
  q: z.string().optional(),
  supplier: z.string().optional(),
  buyer: z.string().optional(),
  problems: z.string().optional(),
});

export const boardRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  validateSearch: (search): BoardSearch => boardSearch.parse(search),
  component: BoardPage,
});
const casesSearch = z.object({
  status: z.string().optional(),
  kind: z.string().optional(),
  po: z.string().optional(),
  days: z.string().optional(),
});

export const casesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/cases",
  validateSearch: (search) => casesSearch.parse(search),
  component: CasesPage,
});
export const caseRoute = child("/cases/$caseId", CaseTimelinePage);
export const exceptionsRoute = child("/exceptions", ExceptionsBoardPage);
export const planningRoute = child("/planning", PlanningPage);
export const planningRunRoute = child("/planning/$runId", PlanningRunPage);
const runsSearch = z.object({
  agent: z.string().optional(),
  model: z.string().optional(),
  status: z.string().optional(),
  since: z.string().optional(),
});

export const runsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/runs",
  validateSearch: (search): RunsSearch => runsSearch.parse(search),
  component: RunsPage,
});
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
    boardRoute,
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
