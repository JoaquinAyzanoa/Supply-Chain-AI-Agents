/**
 * Supplier scores as a table: score bar, on-time in-full, observed lead time,
 * reply time, receipt problems, flagged changes; the scorecard paragraph opens
 * under the row.
 */
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { cn, formatNumber } from "@/lib/utils";
import { SupplierProfileEditor } from "./SupplierProfileEditor";

export interface SupplierScoreRow {
  partner_id: number;
  partner_name: string;
  score: number;
  otif?: number | null;
  lead_time_mean_days?: number | null;
  lead_time_sigma_days?: number | null;
  promise_drift_days?: number | null;
  response_hours_median?: number | null;
  quality_rate?: number | null;
  price_cv?: number | null;
  samples?: Record<string, number>;
  scorecard?: string | null;
  trends?: string[];
}

export function SupplierScoreTable({ rows, withProfile = false }: { rows: SupplierScoreRow[]; withProfile?: boolean }) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState<number | null>(null);
  const pct = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`);
  const days = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${formatNumber(value, locale, 1)} d`);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("suppliers.col.supplier")}</TableHead>
          <TableHead>{t("suppliers.col.score")}</TableHead>
          <TableHead className="text-right">{t("suppliers.col.otif")}</TableHead>
          <TableHead className="text-right">{t("suppliers.col.lead_time")}</TableHead>
          <TableHead className="text-right">{t("suppliers.col.reply")}</TableHead>
          <TableHead className="text-right">{t("suppliers.col.quality")}</TableHead>
          <TableHead>{t("suppliers.col.trends")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <Row key={row.partner_id} row={row} withProfile={withProfile} open={open === row.partner_id} onToggle={() => setOpen(open === row.partner_id ? null : row.partner_id)} pct={pct} days={days} />
        ))}
      </TableBody>
    </Table>
  );
}

function Row({
  row,
  withProfile,
  open,
  onToggle,
  pct,
  days,
}: {
  row: SupplierScoreRow;
  withProfile: boolean;
  open: boolean;
  onToggle: () => void;
  pct: (v: number | null | undefined) => string;
  days: (v: number | null | undefined) => string;
}) {
  const { t, locale } = useI18n();
  const tone = row.score >= 80 ? "bg-success" : row.score >= 60 ? "bg-warning" : "bg-destructive";
  return (
    <>
      <TableRow>
        <TableCell>
          <button type="button" className="text-left font-medium text-primary underline" onClick={onToggle} aria-expanded={open}>
            {row.partner_name}
          </button>
          {row.samples?.lines !== undefined ? <div className="text-xs text-muted-foreground">{t("suppliers.lines", { n: row.samples.lines })}</div> : null}
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2">
            <div className="h-2 w-24 overflow-hidden rounded bg-muted" aria-hidden="true">
              <div className={cn("h-2 rounded", tone)} style={{ width: `${Math.max(0, Math.min(100, row.score))}%` }} />
            </div>
            <span className="tabular-nums" aria-label={t("suppliers.score_of", { n: formatNumber(row.score, locale, 0) })}>
              {formatNumber(row.score, locale, 0)}
            </span>
          </div>
        </TableCell>
        <TableCell className="text-right tabular-nums">{pct(row.otif)}</TableCell>
        <TableCell className="text-right tabular-nums">
          {days(row.lead_time_mean_days)}
          {row.lead_time_sigma_days ? <span className="text-xs text-muted-foreground"> ± {formatNumber(row.lead_time_sigma_days, locale, 1)}</span> : null}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {row.response_hours_median === null || row.response_hours_median === undefined ? "—" : `${formatNumber(row.response_hours_median, locale, 1)} h`}
        </TableCell>
        <TableCell className="text-right tabular-nums">{pct(row.quality_rate)}</TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1">
            {(row.trends ?? []).map((flag) => (
              <Badge key={flag} variant="warning">
                {flag}
              </Badge>
            ))}
          </div>
        </TableCell>
      </TableRow>
      {open ? (
        <TableRow>
          <TableCell colSpan={7} className="bg-muted/30 text-sm">
            {row.scorecard ? <p className="whitespace-pre-line">{row.scorecard}</p> : <p className="text-muted-foreground">{t("suppliers.no_scorecard")}</p>}
            {withProfile ? (
              <div className="mt-3 border-t pt-3">
                <h3 className="mb-2 text-xs font-medium uppercase text-muted-foreground">{t("suppliers.profile.title")}</h3>
                <SupplierProfileEditor partnerId={row.partner_id} />
              </div>
            ) : null}
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
              <div>
                <dt>{t("suppliers.drift")}</dt>
                <dd>{row.promise_drift_days === null || row.promise_drift_days === undefined ? "—" : `${formatNumber(row.promise_drift_days, locale, 1)} d`}</dd>
              </div>
              <div>
                <dt>{t("suppliers.price_cv")}</dt>
                <dd>{row.price_cv === null || row.price_cv === undefined ? "—" : formatNumber(row.price_cv, locale, 3)}</dd>
              </div>
              <div>
                <dt>{t("suppliers.orders")}</dt>
                <dd>{row.samples?.orders ?? "—"}</dd>
              </div>
              <div>
                <dt>{t("suppliers.replies")}</dt>
                <dd>{row.samples?.replies ?? "—"}</dd>
              </div>
            </dl>
          </TableCell>
        </TableRow>
      ) : null}
    </>
  );
}
