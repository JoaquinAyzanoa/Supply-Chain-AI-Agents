/**
 * Home: the department's desk at a glance. The director's paragraph, the KPIs with a
 * trend, what needs you sorted by impact, and what ran alone (revertible while its
 * window is open). Every number links to the page where it is worked.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDownRight, ArrowUpRight, Minus, Sunrise } from "lucide-react";

import { api, unwrap, type Schemas } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { RecordLink } from "@/components/RecordLink";
import { AutoActionsFeed } from "@/features/autonomy/AutoActionsFeed";
import { useBriefing } from "@/features/briefing/BriefingPage";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export type HomeView = Schemas["HomeView"];
export type Kpi = Schemas["Kpi"];

const KPI_PATH: Record<string, string> = {
  service_level: "/suppliers",
  late_orders: "/exceptions",
  pending_approvals: "/approvals",
  spend_month: "/board",
  ai_cost_month: "/ai",
  automated_week: "/autonomy",
};

export function useHome() {
  return useQuery({ queryKey: ["home"], queryFn: async () => unwrap(await api.GET("/api/home")) as HomeView });
}

export function HomePage() {
  const { t, locale } = useI18n();
  const home = useHome();
  const briefing = useBriefing();
  if (home.isPending) return <Loading />;
  if (home.error) return <ErrorBox error={home.error} onRetry={() => home.refetch()} />;
  const data = home.data;
  return (
    <div>
      <PageTitle title={t("home.title")}>
        <span className="text-xs text-muted-foreground">{formatDate(data.as_of, locale)}</span>
      </PageTitle>
      <div className="space-y-6 p-4">
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" aria-label={t("home.kpis")}>
          {data.kpis.map((kpi) => (
            <KpiCard key={kpi.key} kpi={kpi} />
          ))}
        </section>
        <section className="rounded-lg border bg-card p-4" aria-label={t("briefing.title")}>
          <div className="mb-1 flex items-center gap-2 text-sm font-semibold">
            <Sunrise className="h-4 w-4" /> {t("briefing.title")}
            <Link to="/briefing" className="ml-auto text-xs font-normal text-primary underline">
              {t("home.open_briefing")}
            </Link>
          </div>
          {briefing.data?.paragraph ? (
            <p className="text-sm leading-relaxed">{briefing.data.paragraph}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{briefing.data ? t("briefing.no_paragraph") : t("home.no_briefing")}</p>
          )}
        </section>
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-lg border bg-card p-4" aria-label={t("home.needs_you")}>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              {t("home.needs_you")}
              <Badge variant={data.pending ? "warning" : "secondary"}>{data.pending}</Badge>
            </h2>
            {data.needs_you.length === 0 ? (
              <Empty text={t("home.nothing_pending")} />
            ) : (
              <ul className="space-y-1 text-sm">
                {data.needs_you.map((item, index) => (
                  <li key={index}>
                    {item.path ? (
                      <RecordLink path={item.path} className="text-primary underline">
                        {item.text}
                      </RecordLink>
                    ) : (
                      item.text
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="rounded-lg border bg-card p-4" aria-label={t("autonomy.feed.title")}>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              {t("autonomy.feed.title")}
              <Link to="/autonomy" className="ml-auto text-xs font-normal text-primary underline">
                {t("home.open_feed")}
              </Link>
            </h2>
            <AutoActionsFeed days={7} compact />
          </section>
        </div>
      </div>
    </div>
  );
}

function KpiCard({ kpi }: { kpi: Kpi }) {
  const { t, locale } = useI18n();
  const value = format(kpi, kpi.value, locale);
  const previous = kpi.previous;
  const delta = kpi.value !== null && kpi.value !== undefined && previous !== null && previous !== undefined && kpi.key !== "pending_approvals" ? kpi.value - previous : null;
  const goodWhenUp = kpi.key === "service_level" || kpi.key === "automated_week";
  const tone = delta === null || delta === 0 ? "text-muted-foreground" : (delta > 0) === goodWhenUp ? "text-success-text" : "text-destructive";
  const Arrow = delta === null || delta === 0 ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  const path = KPI_PATH[kpi.key] ?? "/";
  return (
    <RecordLink path={path} className="block rounded-lg border bg-card p-3 hover:bg-accent/40">
      <div className="text-xs text-muted-foreground">{t(`home.kpi.${kpi.key}`)}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      <div className={cn("mt-1 flex items-center gap-1 text-xs", tone)}>
        {delta !== null ? (
          <>
            <Arrow className="h-3 w-3" /> {t("home.vs_previous", { value: format(kpi, previous, locale) })}
          </>
        ) : (
          <span className="text-muted-foreground">{kpi.detail ?? ""}</span>
        )}
      </div>
    </RecordLink>
  );
}

function format(kpi: Kpi, value: number | null | undefined, locale: string): string {
  if (value === null || value === undefined) return "—";
  if (kpi.unit === "pct") return `${formatNumber(value, locale, 1)}%`;
  if (kpi.unit === "usd") return `$${formatNumber(value, locale, 2)}`;
  if (kpi.unit === "money") return `${formatNumber(value, locale, 0)} ${kpi.currency ?? ""}`.trim();
  return formatNumber(value, locale, 0);
}
