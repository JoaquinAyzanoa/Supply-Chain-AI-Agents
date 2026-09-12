/**
 * Runs: the agents' executions (model, tokens, cost, duration, trace) with
 * filters, and the scheduler's firings with each job's cron. This is where a
 * model change made in Settings is read back the next morning.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { api, unwrap } from "@/api/client";
import { StatusBadge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RunsTable } from "@/features/cases/CaseTimeline";
import { agentName } from "@/features/cases/labels";
import { useI18n } from "@/i18n";
import { formatDateTime, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export interface RunsSearch {
  agent?: string;
  model?: string;
  status?: string;
  since?: string;
}

const AGENTS = ["supplier_comms", "inventory_planning", "director"] as const;
const STATUSES = ["running", "sent", "applied", "awaiting_approval", "rejected", "no_action", "escalated", "failed"] as const;

export function useAgentRuns(search: RunsSearch) {
  return useQuery({
    queryKey: ["runs", "agents", search],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/runs", {
          params: {
            query: {
              agent: search.agent || undefined,
              model: search.model || undefined,
              status: search.status || undefined,
              since: search.since ? new Date(search.since).toISOString() : undefined,
              limit: 200,
            },
          },
        }),
      ),
  });
}

export function useSchedulerRuns() {
  return useQuery({
    queryKey: ["runs", "scheduler"],
    queryFn: async () => unwrap(await api.GET("/api/runs/scheduler", { params: { query: { limit: 50 } } })),
  });
}

export function RunsPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as RunsSearch;
  const runs = useAgentRuns(search);
  const scheduler = useSchedulerRuns();
  const setSearch = (patch: Partial<RunsSearch>) =>
    void navigate({
      to: "/runs",
      search: (prev: RunsSearch) => Object.fromEntries(Object.entries({ ...prev, ...patch }).filter(([, v]) => v)) as RunsSearch,
    });
  const totals = (runs.data ?? []).reduce(
    (acc, run) => ({ cost: acc.cost + run.cost_usd, tokens: acc.tokens + run.input_tokens + run.output_tokens }),
    { cost: 0, tokens: 0 },
  );

  return (
    <div>
      <PageTitle title={t("runs.title")}>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label={t("runs.filter.agent")} className="h-8 rounded-md border bg-card px-2 text-sm" value={search.agent ?? ""} onChange={(e) => setSearch({ agent: e.target.value })}>
            <option value="">{t("runs.filter.all_agents")}</option>
            {AGENTS.map((agent) => (
              <option key={agent} value={agent}>
                {agentName(t, agent)}
              </option>
            ))}
          </select>
          <select aria-label={t("runs.filter.status")} className="h-8 rounded-md border bg-card px-2 text-sm" value={search.status ?? ""} onChange={(e) => setSearch({ status: e.target.value })}>
            <option value="">{t("runs.filter.all_statuses")}</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {status.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <Input
            aria-label={t("runs.filter.model")}
            placeholder={t("runs.filter.model")}
            className="h-8 w-40"
            defaultValue={search.model ?? ""}
            onKeyDown={(e) => {
              if (e.key === "Enter") setSearch({ model: (e.target as HTMLInputElement).value });
            }}
            onBlur={(e) => setSearch({ model: e.target.value })}
          />
          <Input aria-label={t("runs.filter.since")} type="date" className="h-8 w-40" value={search.since ?? ""} onChange={(e) => setSearch({ since: e.target.value })} />
        </div>
      </PageTitle>
      <section className="p-4">
        <h2 className="mb-2 flex flex-wrap items-baseline gap-3 text-sm font-semibold">
          {t("runs.agents")}
          {runs.data ? (
            <span className="font-normal text-muted-foreground">
              {t("runs.totals", { n: runs.data.length, tokens: formatNumber(totals.tokens, locale, 0), cost: formatNumber(totals.cost, locale, 4) })}
            </span>
          ) : null}
        </h2>
        {runs.isPending ? <Loading /> : null}
        {runs.error ? <ErrorBox error={runs.error} onRetry={() => runs.refetch()} /> : null}
        {runs.data && runs.data.length === 0 ? <Empty /> : null}
        {runs.data && runs.data.length ? <RunsTable runs={runs.data} showCase /> : null}
      </section>
      <section className="p-4">
        <h2 className="mb-2 text-sm font-semibold">{t("runs.scheduler")}</h2>
        {scheduler.isPending ? <Loading /> : null}
        {scheduler.error ? <ErrorBox error={scheduler.error} onRetry={() => scheduler.refetch()} /> : null}
        {scheduler.data && scheduler.data.length === 0 ? <Empty /> : null}
        {scheduler.data && scheduler.data.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("runs.col.started")}</TableHead>
                <TableHead>{t("runs.col.job")}</TableHead>
                <TableHead>{t("runs.col.trigger")}</TableHead>
                <TableHead>{t("runs.col.status")}</TableHead>
                <TableHead>{t("runs.col.cron")}</TableHead>
                <TableHead>{t("runs.col.summary")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {scheduler.data.map((run) => (
                <TableRow key={run.run_id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(run.started_at, locale)}</TableCell>
                  <TableCell className="font-medium">{run.job_id}</TableCell>
                  <TableCell>{run.trigger}</TableCell>
                  <TableCell>
                    <StatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{run.cron ?? ""}</TableCell>
                  <TableCell className="max-w-[28rem] truncate" title={run.summary ?? ""}>
                    {run.summary ?? ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </section>
    </div>
  );
}
