/**
 * AI performance: the page to show a visitor. How much runs alone, how fast people
 * decide, how often they edit or reject, how good the predictions are, and what the
 * agents cost. Every measure comes from records; a missing one shows as a dash.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export type AiPerformance = Schemas["AiPerformance"];

export function useAiPerformance(days: number) {
  return useQuery({
    queryKey: ["ai", days],
    queryFn: async () => unwrap(await api.GET("/api/ai", { params: { query: { days } } })) as AiPerformance,
  });
}

export function AiPage() {
  const { t, locale } = useI18n();
  const [days, setDays] = useState(30);
  const ai = useAiPerformance(days);
  if (ai.isPending) return <Loading />;
  if (ai.error) return <ErrorBox error={ai.error} onRetry={() => ai.refetch()} />;
  const data = ai.data;
  const pct = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "—" : `${formatNumber(v * 100, locale, digits)}%`);
  return (
    <div>
      <PageTitle title={t("ai.title")}>
        <select aria-label={t("ai.period")} className="h-8 rounded-md border bg-card px-2 text-sm" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90].map((d) => (
            <option key={d} value={d}>
              {t("ai.last_days", { n: d })}
            </option>
          ))}
        </select>
      </PageTitle>
      <div className="space-y-6 p-4">
        <p className="max-w-3xl text-sm text-muted-foreground">{t("ai.intro")}</p>
        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={t("ai.measures")}>
          <Measure label={t("ai.automation_rate")} value={pct(data.automation_rate)} hint={t("ai.decisions", { n: data.decisions })} />
          <Measure label={t("ai.turnaround")} value={data.turnaround_hours_median === null || data.turnaround_hours_median === undefined ? "—" : `${formatNumber(data.turnaround_hours_median, locale, 1)} h`} hint={t("ai.turnaround_hint")} />
          <Measure label={t("ai.edit_rate")} value={pct(data.edit_rate)} hint={t("ai.edit_hint")} />
          <Measure label={t("ai.rejection_rate")} value={pct(data.rejection_rate)} hint={t("ai.rejection_hint")} />
          <Measure label={t("ai.eta_error")} value={data.eta_error_days === null || data.eta_error_days === undefined ? "—" : t("risk.days", { n: formatNumber(data.eta_error_days, locale, 1) })} hint={t("ai.eta_hint")} />
          <Measure label={t("ai.invoices")} value={pct(data.invoices_first_time_rate)} hint={t("ai.invoices_hint", { n: data.invoices_first_time, total: data.invoices_total })} />
          <Measure label={t("ai.savings")} value={data.negotiation_savings === null || data.negotiation_savings === undefined ? "—" : formatNumber(data.negotiation_savings, locale, 0)} hint={t("ai.savings_hint")} />
          <Measure label={t("ai.cost_per_case")} value={data.cost_per_case_usd === null || data.cost_per_case_usd === undefined ? "—" : `$${formatNumber(data.cost_per_case_usd, locale, 3)}`} hint={t("ai.cost_hint", { total: formatNumber(data.cost_usd, locale, 2), runs: data.runs })} />
        </section>
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-lg border bg-card p-3" aria-label={t("ai.by_kind")}>
            <h2 className="mb-2 text-sm font-semibold">{t("ai.by_kind")}</h2>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("ai.col.kind")}</TableHead>
                  <TableHead className="text-right">{t("ai.col.automated")}</TableHead>
                  <TableHead className="text-right">{t("ai.col.decided")}</TableHead>
                  <TableHead className="text-right">{t("ai.col.rate")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.automation.map((row) => (
                  <TableRow key={row.kind}>
                    <TableCell>{t(`approvals.kind.${row.kind}`)}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.automated}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.decided}</TableCell>
                    <TableCell className="text-right">
                      <Badge variant={row.rate === null || row.rate === undefined ? "secondary" : row.rate >= 0.5 ? "success" : "warning"}>{pct(row.rate)}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
                {data.automation.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-muted-foreground">
                      {t("ai.none")}
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </section>
          <section className="space-y-4">
            <section className="rounded-lg border bg-card p-3" aria-label={t("ai.wape")}>
              <h2 className="mb-2 text-sm font-semibold">{t("ai.wape")}</h2>
              {data.wape_by_class.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("ai.none")}</p>
              ) : (
                <ul className="flex flex-wrap gap-3 text-sm">
                  {data.wape_by_class.map((row) => (
                    <li key={row.abc_class} className="rounded-md border px-3 py-1">
                      <span className="font-medium">{t("ai.class", { c: row.abc_class })}</span> · {pct(row.wape)} · {t("ai.lines", { n: row.lines })}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section className="rounded-lg border bg-card p-3" aria-label={t("ai.by_agent")}>
              <h2 className="mb-2 text-sm font-semibold">{t("ai.by_agent")}</h2>
              {data.by_agent.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("ai.none")}</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {data.by_agent.map((row) => (
                    <li key={row.agent} className="flex justify-between tabular-nums">
                      <span>
                        {row.agent} · {t("ai.runs", { n: row.runs })}
                      </span>
                      <span>${formatNumber(row.cost_usd, locale, 3)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </section>
        </div>
      </div>
    </div>
  );
}

function Measure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint ? <div className="mt-1 text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}
