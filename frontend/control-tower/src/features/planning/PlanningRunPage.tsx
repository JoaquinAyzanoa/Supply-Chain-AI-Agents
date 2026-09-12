/**
 * Review one planning run: lines grouped by supplier, editable quantities and
 * min/max, a drawer per row with the explanation, the parameters, a 90-day
 * demand sparkline and a what-if form. Approving the selected lines resolves
 * the run's approval with the accepted ids and the edits: one call, and the
 * planner applies exactly that.
 */
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, Check, FlaskConical, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useResolveApproval } from "@/features/approvals/api";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatMoney, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { ACTIONABLE, useLineDemand, usePlanningRun, useWhatIf, type PlanningLineRow, type PlanningOverrides, type ReplenishmentLine } from "./api";
import { Sparkline } from "./Sparkline";

type Editable = "order_qty" | "proposed_min" | "proposed_max";
type Edits = Record<string, Partial<Record<Editable, number>>>;

export function PlanningRunPage() {
  const { runId } = useParams({ strict: false }) as { runId: string };
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const detail = usePlanningRun(runId);
  const resolve = useResolveApproval();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [edits, setEdits] = useState<Edits>({});
  const [openLine, setOpenLine] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const rows = detail.data?.lines ?? [];
  useEffect(() => {
    setSelected(new Set(rows.filter((r) => ACTIONABLE.has(r.line.action)).map((r) => r.line.line_id)));
    setEdits({});
  }, [detail.data?.run.run_id, rows.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const by = new Map<string, PlanningLineRow[]>();
    for (const row of rows) {
      const key = row.line.supplier_name ?? t("planning.no_supplier");
      by.set(key, [...(by.get(key) ?? []), row]);
    }
    return [...by.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [rows, t]);

  if (detail.isPending) return <Loading />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;
  const run = detail.data.run;
  const canApprove = run.status === "awaiting_approval" && run.approval_id !== null && run.approval_id !== undefined && hasRole("approver");
  const dirty = Object.keys(edits).length;

  const value = (line: ReplenishmentLine, field: Editable): number => edits[line.line_id]?.[field] ?? line[field];
  const setValue = (line: ReplenishmentLine, field: Editable, raw: string) => {
    const parsed = Number(raw);
    if (Number.isNaN(parsed) || parsed < 0) return;
    setEdits((prev) => {
      const next = { ...prev, [line.line_id]: { ...prev[line.line_id], [field]: parsed } };
      if (parsed === line[field]) {
        delete next[line.line_id]![field];
        if (!Object.keys(next[line.line_id]!).length) delete next[line.line_id];
      }
      return next;
    });
  };
  const toggle = (id: string, on: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const approve = async () => {
    if (run.approval_id === null || run.approval_id === undefined) return;
    setMessage(null);
    const accepted = [...selected];
    const lineEdits = Object.fromEntries(Object.entries(edits).filter(([id]) => selected.has(id)));
    try {
      await resolve.mutateAsync({
        id: run.approval_id,
        status: "approved",
        edited_payload: { accepted_line_ids: accepted, edits: lineEdits },
      });
      setMessage(t("planning.approved", { n: accepted.length }));
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const totalValue = rows
    .filter((r) => selected.has(r.line.line_id))
    .reduce((sum, r) => sum + value(r.line, "order_qty") * (r.line.unit_price ?? 0), 0);

  return (
    <div>
      <PageTitle title={`${t("planning.run")} · ${formatDate(run.as_of, locale)}`}>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={run.status} />
          <Badge variant="outline">{t(`planning.kind.${run.kind}`)}</Badge>
          {run.approval_id ? (
            <Link to="/approvals" search={{ id: run.approval_id, tab: run.status === "awaiting_approval" ? undefined : "resolved" }} className="text-sm text-primary underline">
              {t("planning.approval_link", { id: run.approval_id })}
            </Link>
          ) : null}
          <Link to="/planning" className="inline-flex items-center gap-1 text-sm text-muted-foreground">
            <ArrowLeft className="h-3 w-3" /> {t("planning.all_runs")}
          </Link>
        </div>
      </PageTitle>
      {run.summary ? <p className="whitespace-pre-line px-4 pt-3 text-sm">{run.summary}</p> : null}
      {rows.length === 0 ? <Empty /> : null}
      <div className="flex flex-col gap-4 p-4 lg:flex-row">
        <div className="min-w-0 flex-1">
          {groups.map(([supplier, lines]) => (
            <section key={supplier} className="mb-4">
              <h2 className="mb-1 text-sm font-semibold">
                {supplier} <span className="font-normal text-muted-foreground">({lines.length})</span>
              </h2>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-6" />
                    <TableHead>{t("planning.col.product")}</TableHead>
                    <TableHead>{t("planning.col.action")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.position")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.forecast")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.rop")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.min_max")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.qty")}</TableHead>
                    <TableHead className="text-right">{t("planning.col.value")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {lines.map(({ line, accepted, applied }) => {
                    const editable = canApprove && ACTIONABLE.has(line.action);
                    const isDirty = Boolean(edits[line.line_id]);
                    return (
                      <TableRow
                        key={line.line_id}
                        className={cn(openLine === line.line_id ? "bg-accent/40" : "", isDirty ? "bg-warning/10" : "")}
                      >
                        <TableCell>
                          <input
                            type="checkbox"
                            aria-label={line.product_ref}
                            checked={selected.has(line.line_id)}
                            disabled={!editable}
                            onChange={(event) => toggle(line.line_id, event.target.checked)}
                          />
                        </TableCell>
                        <TableCell>
                          <button type="button" className="text-left font-medium text-primary underline" onClick={() => setOpenLine(openLine === line.line_id ? null : line.line_id)}>
                            {line.product_ref}
                          </button>
                          {line.product_name ? <div className="max-w-[16rem] truncate text-xs text-muted-foreground">{line.product_name}</div> : null}
                          {line.exception ? <Badge variant="warning">{line.exception}</Badge> : null}
                          {applied ? <Badge variant="success">{t("planning.applied")}</Badge> : accepted === false ? <Badge variant="secondary">{t("planning.skipped")}</Badge> : null}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{t(`planning.action.${line.action}`)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(line.position, locale, 0)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(line.forecast_daily, locale, 2)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(line.rop, locale, 0)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          <div className="flex items-center justify-end gap-1">
                            <NumberCell label={`${line.product_ref} min`} editable={editable} value={value(line, "proposed_min")} onChange={(v) => setValue(line, "proposed_min", v)} />
                            /
                            <NumberCell label={`${line.product_ref} max`} editable={editable} value={value(line, "proposed_max")} onChange={(v) => setValue(line, "proposed_max", v)} />
                          </div>
                          {line.current_min !== null && line.current_min !== undefined ? (
                            <div className="text-xs text-muted-foreground">
                              {t("planning.now")} {formatNumber(line.current_min, locale, 0)} / {formatNumber(line.current_max, locale, 0)}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          <NumberCell label={`${line.product_ref} qty`} editable={editable} value={value(line, "order_qty")} onChange={(v) => setValue(line, "order_qty", v)} />
                          {line.moq ? <div className="text-xs text-muted-foreground">MOQ {formatNumber(line.moq, locale, 0)}</div> : null}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(value(line, "order_qty") * (line.unit_price ?? 0), line.currency, locale)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </section>
          ))}
        </div>
        {openLine ? (
          <LineDrawer
            runId={runId}
            row={rows.find((r) => r.line.line_id === openLine)!}
            onClose={() => setOpenLine(null)}
          />
        ) : null}
      </div>
      {rows.length ? (
        <div className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t bg-card p-3 text-sm">
          <span>{t("planning.selected", { n: selected.size, value: formatMoney(totalValue, rows[0]?.line.currency, locale) })}</span>
          {dirty ? <Badge variant="warning">{t("planning.edited", { n: dirty })}</Badge> : null}
          {canApprove ? (
            <Button onClick={approve} disabled={resolve.isPending || selected.size === 0}>
              <Check className="h-4 w-4" /> {t("planning.approve", { n: selected.size })}
            </Button>
          ) : null}
          {message ? (
            <span className="text-muted-foreground" role="status">
              {message}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function NumberCell({ label, editable, value, onChange }: { label: string; editable: boolean; value: number; onChange: (raw: string) => void }) {
  const { locale } = useI18n();
  if (!editable) return <span>{formatNumber(value, locale, 2)}</span>;
  return (
    <Input
      aria-label={label}
      type="number"
      min={0}
      step="any"
      className="h-7 w-20 px-1 text-right text-sm"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function LineDrawer({ runId, row, onClose }: { runId: string; row: PlanningLineRow; onClose: () => void }) {
  const { t, locale } = useI18n();
  const line = row.line;
  const demand = useLineDemand(runId, line.line_id);
  const whatIf = useWhatIf(runId);
  const [overrides, setOverrides] = useState<PlanningOverrides>({});
  useEffect(() => {
    setOverrides({});
    whatIf.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line.line_id]);

  const number = (key: keyof PlanningOverrides) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const raw = event.target.value;
    setOverrides((prev) => ({ ...prev, [key]: raw === "" ? undefined : Number(raw) }));
  };
  const simulated = whatIf.data?.simulated;
  const param = (label: string, base: number | null | undefined, sim: number | null | undefined, digits = 2) => (
    <tr key={label}>
      <td className="pr-3 text-muted-foreground">{label}</td>
      <td className="text-right tabular-nums">{formatNumber(base, locale, digits)}</td>
      {simulated ? (
        <td className={cn("text-right tabular-nums", sim !== base ? "font-semibold text-primary" : "")}>{formatNumber(sim, locale, digits)}</td>
      ) : null}
    </tr>
  );

  return (
    <aside className="w-full shrink-0 rounded-lg border bg-card p-3 text-sm lg:w-96" aria-label={t("planning.drawer")}>
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold">{line.product_ref}</div>
          <div className="text-xs text-muted-foreground">{line.product_name}</div>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          {t("planning.close")}
        </Button>
      </div>
      {line.explanation ? (
        <p className="mb-3 flex gap-2 rounded-md bg-muted/50 p-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <span>{line.explanation}</span>
        </p>
      ) : null}
      <div className="mb-3">
        <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("planning.demand_90")}</div>
        {demand.isPending ? <Loading /> : null}
        {demand.error ? <p className="text-xs text-destructive">{t("planning.demand_unavailable")}</p> : null}
        {demand.data ? <Sparkline demand={demand.data} /> : null}
        <div className="text-xs text-muted-foreground">
          {t("planning.forecast_line", { method: line.forecast_method, rate: formatNumber(line.forecast_daily, locale, 2), wape: line.wape !== null && line.wape !== undefined ? `${Math.round(line.wape * 100)}%` : "—" })}
        </div>
      </div>
      <table className="mb-3 w-full text-xs">
        <thead>
          <tr className="text-muted-foreground">
            <th className="text-left font-medium">{t("planning.param")}</th>
            <th className="text-right font-medium">{t("planning.baseline")}</th>
            {simulated ? <th className="text-right font-medium">{t("planning.simulated")}</th> : null}
          </tr>
        </thead>
        <tbody>
          {param(t("planning.p.service_level"), line.service_level, simulated?.service_level, 3)}
          {param(t("planning.p.lead_time"), line.lead_time_days, simulated?.lead_time_days, 1)}
          {param(t("planning.p.review_period"), line.review_period_days, simulated?.review_period_days, 0)}
          {param(t("planning.p.ss"), line.ss, simulated?.ss)}
          {param(t("planning.p.rop"), line.rop, simulated?.rop)}
          {param(t("planning.p.order_up_to"), line.order_up_to, simulated?.order_up_to)}
          {param(t("planning.p.order_qty"), line.order_qty, simulated?.order_qty)}
          {param(t("planning.p.coverage"), line.coverage_days, simulated?.coverage_days, 0)}
        </tbody>
      </table>
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          whatIf.mutate({ line_id: line.line_id, overrides });
        }}
      >
        <div className="text-xs font-medium uppercase text-muted-foreground">{t("planning.what_if")}</div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="wi-sl">{t("planning.p.service_level")}</Label>
            <Input id="wi-sl" type="number" step="0.01" min="0.51" max="0.99" value={overrides.service_level ?? ""} onChange={number("service_level")} />
          </div>
          <div>
            <Label htmlFor="wi-lt">{t("planning.p.lead_time")}</Label>
            <Input id="wi-lt" type="number" step="1" min="0" value={overrides.lead_time_days ?? ""} onChange={number("lead_time_days")} />
          </div>
          <div>
            <Label htmlFor="wi-rp">{t("planning.p.review_period")}</Label>
            <Input id="wi-rp" type="number" step="1" min="1" value={overrides.review_period_days ?? ""} onChange={number("review_period_days")} />
          </div>
          <div>
            <Label htmlFor="wi-mc">{t("planning.p.max_coverage")}</Label>
            <Input id="wi-mc" type="number" step="1" min="1" value={overrides.max_coverage_days ?? ""} onChange={number("max_coverage_days")} />
          </div>
        </div>
        <Button type="submit" variant="outline" size="sm" disabled={whatIf.isPending}>
          <FlaskConical className="h-4 w-4" /> {whatIf.isPending ? t("planning.simulating") : t("planning.simulate")}
        </Button>
        {whatIf.error ? <p className="text-xs text-destructive">{whatIf.error.message}</p> : null}
        {simulated?.explanation ? <p className="text-xs text-muted-foreground">{simulated.explanation}</p> : null}
      </form>
    </aside>
  );
}
