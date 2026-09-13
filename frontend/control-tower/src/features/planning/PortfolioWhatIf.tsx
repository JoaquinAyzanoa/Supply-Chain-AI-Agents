/**
 * Portfolio what-if: change the service level or the review period of a whole ABC
 * class and see stock value, expected stockouts and order count before approving.
 * Nothing is written; the planner recomputes the class in a simulation run.
 */
import { useMutation } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatNumber } from "@/lib/utils";

export interface PortfolioTotals {
  lines: number;
  orders: number;
  spend: number;
  stock_value: number;
  expected_stockouts: number;
  service_level?: number | null;
}

export interface ClassWhatIf {
  abc_class: string;
  baseline: PortfolioTotals;
  simulated: PortfolioTotals;
  run_id: string;
}

export function PortfolioWhatIf({ runId }: { runId: string }) {
  const { t, locale } = useI18n();
  const [abcClass, setAbcClass] = useState<"A" | "B" | "C">("A");
  const [serviceLevel, setServiceLevel] = useState("0.97");
  const [reviewDays, setReviewDays] = useState("");
  const simulate = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/planning/runs/{run_id}/what-if-class", {
          params: { path: { run_id: runId } },
          body: {
            abc_class: abcClass,
            overrides: {
              service_level: serviceLevel ? Number(serviceLevel) : null,
              review_period_days: reviewDays ? Number(reviewDays) : null,
              lead_time_days: null,
              max_coverage_days: null,
            },
          },
        }),
      ) as unknown as ClassWhatIf,
  });
  const rows: { key: keyof PortfolioTotals; label: string; digits: number }[] = [
    { key: "orders", label: t("whatif.orders"), digits: 0 },
    { key: "spend", label: t("whatif.spend"), digits: 0 },
    { key: "stock_value", label: t("whatif.stock_value"), digits: 0 },
    { key: "expected_stockouts", label: t("whatif.stockouts"), digits: 2 },
    { key: "service_level", label: t("whatif.service_level"), digits: 3 },
  ];
  return (
    <section className="space-y-2 rounded-md border bg-card p-3" aria-label={t("whatif.title")}>
      <h3 className="text-sm font-semibold">{t("whatif.title")}</h3>
      <p className="text-xs text-muted-foreground">{t("whatif.intro")}</p>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          simulate.mutate();
        }}
      >
        <div className="flex flex-col gap-1">
          <Label htmlFor="cls-wi-class">{t("whatif.class")}</Label>
          <select id="cls-wi-class" className="h-9 rounded-md border bg-background px-2 text-sm" value={abcClass} onChange={(e) => setAbcClass(e.target.value as "A" | "B" | "C")}>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="cls-wi-sl">{t("whatif.service_level")}</Label>
          <Input id="cls-wi-sl" type="number" step="0.01" min={0.51} max={0.99} value={serviceLevel} onChange={(e) => setServiceLevel(e.target.value)} className="w-28" />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="cls-wi-rp">{t("whatif.review_days")}</Label>
          <Input id="cls-wi-rp" type="number" min={1} value={reviewDays} onChange={(e) => setReviewDays(e.target.value)} className="w-28" placeholder="—" />
        </div>
        <Button type="submit" size="sm" disabled={simulate.isPending}>
          <FlaskConical className="h-4 w-4" /> {t("whatif.run")}
        </Button>
      </form>
      {simulate.error ? <p className="text-sm text-destructive">{simulate.error instanceof Error ? simulate.error.message : String(simulate.error)}</p> : null}
      {simulate.data ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                {t("whatif.class")} {simulate.data.abc_class}
              </TableHead>
              <TableHead className="text-right">{t("whatif.baseline")}</TableHead>
              <TableHead className="text-right">{t("whatif.simulated")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const before = simulate.data.baseline[row.key];
              const after = simulate.data.simulated[row.key];
              return (
                <TableRow key={row.key}>
                  <TableCell>{row.label}</TableCell>
                  <TableCell className="text-right tabular-nums">{before === null || before === undefined ? "—" : formatNumber(before, locale, row.digits)}</TableCell>
                  <TableCell className="text-right font-medium tabular-nums">{after === null || after === undefined ? "—" : formatNumber(after, locale, row.digits)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : null}
    </section>
  );
}
