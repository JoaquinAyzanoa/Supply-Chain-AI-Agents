/**
 * The signed-in frame: navigation, the language switch, the live indicator and
 * the user menu. Screens render in the outlet. Navigation collapses to a
 * bottom bar on phones.
 */
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Activity, CalendarClock, ClipboardCheck, History, Kanban, LogOut, Settings } from "lucide-react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useApprovals } from "@/features/approvals/api";
import { NotificationsBell } from "./NotificationsBell";
import { LANGUAGES, useI18n } from "@/i18n";
import { cn } from "@/lib/utils";
import { useStream } from "@/realtime/useStream";

const NAV = [
  { to: "/", key: "nav.board", icon: Kanban, role: "viewer" },
  { to: "/approvals", key: "nav.approvals", icon: ClipboardCheck, role: "viewer" },
  { to: "/cases", key: "nav.cases", icon: History, role: "viewer" },
  { to: "/planning", key: "nav.planning", icon: CalendarClock, role: "viewer" },
  { to: "/runs", key: "nav.runs", icon: Activity, role: "viewer" },
  { to: "/settings", key: "nav.settings", icon: Settings, role: "admin" },
] as const;

export function AppShell() {
  const { session, logout, hasRole } = useAuth();
  const { t, language, setLanguage } = useI18n();
  const status = useStream(session?.token ?? null);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const items = NAV.filter((item) => hasRole(item.role));
  // The inbox count sits on the nav entry: most approvals are resolved from the board.
  const pending = useApprovals({ status: "pending" });
  const pendingCount = pending.data?.length ?? 0;

  return (
    <div className="flex min-h-screen flex-col sm:flex-row">
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-card p-3 sm:flex">
        <div className="mb-4 px-2 text-lg font-semibold">{t("app.title")}</div>
        <nav className="flex flex-col gap-1" aria-label="main">
          {items.map((item) => (
            <NavLink key={item.to} to={item.to} active={isActive(pathname, item.to)}>
              <item.icon className="h-4 w-4" /> {t(item.key)}
              {item.to === "/approvals" && pendingCount ? (
                <Badge variant="warning" className="ml-auto" aria-label={t("nav.pending", { n: pendingCount })}>
                  {pendingCount}
                </Badge>
              ) : null}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-2 px-2 pt-4 text-xs text-muted-foreground">
          <NotificationsBell />
          <LiveDot status={status} label={status === "open" ? t("app.live") : t("app.offline")} />
          <div className="truncate" title={session?.user.email}>
            {session?.user.name} · {t(`role.${session?.user.role ?? "viewer"}`)}
          </div>
          <LanguageSwitch language={language} setLanguage={setLanguage} label={t("app.language")} />
          <Button variant="ghost" size="sm" className="justify-start px-0" onClick={logout}>
            <LogOut className="h-4 w-4" /> {t("app.signout")}
          </Button>
        </div>
      </aside>

      <header className="flex items-center justify-between border-b bg-card px-4 py-2 sm:hidden">
        <span className="font-semibold">{t("app.title")}</span>
        <div className="flex items-center gap-2">
          <NotificationsBell compact />
          <LiveDot status={status} label="" />
          <LanguageSwitch language={language} setLanguage={setLanguage} label={t("app.language")} compact />
          <Button variant="ghost" size="icon" aria-label={t("app.signout")} onClick={logout}>
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </header>

      <main className="flex-1 pb-16 sm:pb-0">
        <Outlet />
      </main>

      <nav className="fixed inset-x-0 bottom-0 flex justify-around border-t bg-card py-1 sm:hidden" aria-label="main">
        {items.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className={cn(
              "flex flex-col items-center gap-0.5 px-2 py-1 text-[10px]",
              isActive(pathname, item.to) ? "text-primary" : "text-muted-foreground",
            )}
          >
            <span className="relative">
              <item.icon className="h-5 w-5" />
              {item.to === "/approvals" && pendingCount ? (
                <span className="absolute -right-2 -top-1 min-w-4 rounded-full bg-warning px-1 text-center text-[10px] font-semibold leading-4 text-foreground">
                  {pendingCount}
                </span>
              ) : null}
            </span>
            {t(item.key)}
          </Link>
        ))}
      </nav>
    </div>
  );
}

function isActive(pathname: string, to: string): boolean {
  return to === "/" ? pathname === "/" : pathname.startsWith(to);
}

function NavLink({ to, active, children }: { to: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
        active ? "bg-accent text-accent-foreground font-medium" : "text-muted-foreground hover:bg-accent/60",
      )}
    >
      {children}
    </Link>
  );
}

function LiveDot({ status, label }: { status: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5" title={status}>
      <span
        className={cn(
          "inline-block h-2 w-2 rounded-full",
          status === "open" ? "bg-success" : status === "reconnecting" ? "bg-warning" : "bg-muted-foreground/40",
        )}
      />
      {label}
    </span>
  );
}

export function LanguageSwitch({
  language,
  setLanguage,
  label,
  compact = false,
}: {
  language: string;
  setLanguage: (language: "en" | "es") => void;
  label: string;
  compact?: boolean;
}) {
  return (
    <div className="flex items-center gap-1" role="group" aria-label={label}>
      {LANGUAGES.map((option) => (
        <button
          key={option.code}
          type="button"
          onClick={() => setLanguage(option.code)}
          aria-pressed={language === option.code}
          className={cn(
            "rounded px-1.5 py-0.5 text-xs uppercase",
            language === option.code ? "bg-accent font-semibold text-accent-foreground" : "text-muted-foreground",
          )}
        >
          {compact ? option.code : option.label}
        </button>
      ))}
    </div>
  );
}
