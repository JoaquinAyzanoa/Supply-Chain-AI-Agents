/**
 * The risk radar: which products may run out in 30 or 60 days, which suppliers may
 * deliver late, and the money on the line. One click on a product starts the right
 * sourcing move through the agents; a person still approves the result.
 */
import { Link } from "@tanstack/react-router";
import { Siren, Zap } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { useActOnRisk, useRiskReport, type ProductRisk } from "./api";

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function tone(p: number): string {
  if (p >= 0.5) return "bg-destructive/15 text-destructive";
  if (p >= 0.2) return "bg-warning/20 text-warning-text";
  return "bg-success/15 text-success-text";
}

export function RiskPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const report = useRiskReport();
  const act = useActOnRisk();
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");
  if (report.isPending) return <Loading />;
  if (report.error) return <ErrorBox error={report.error} onRetry={() => report.refetch()} />;
  const data = report.data;
  const run = async (product: ProductRisk) => {
    setMessage(null);
    try {
      const result = await act.mutateAsync({ product_id: product.product_id });
      setMessage(result.summary);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <div>
      <PageTitle title={t("risk.title")}>
        <span className="text-xs text-muted-foreground">{t("risk.as_of", { date: formatDate(data.as_of, locale), warehouse: data.warehouse_code })}</span>
      </PageTitle>
      <div className="space-y-6 p-4">
        <p className="max-w-3xl text-sm text-muted-foreground">{t("risk.intro")}</p>
        <div className="flex flex-wrap gap-3">
          <Badge variant={data.at_risk_30 ? "destructive" : "success"} className="gap-1">
            <Siren className="h-3 w-3" /> {t("risk.at_risk", { n: data.at_risk_30 })}
          </Badge>
          <Badge variant="secondary">{t("risk.exposure", { amount: formatNumber(data.cash_exposure, locale, 0) })}</Badge>
        </div>
        {message ? (
          <p className="text-sm text-muted-foreground" role="status">
            {message}
          </p>
        ) : null}

        <section className="space-y-2">
          <h2 className="text-base font-semibold">{t("risk.products")}</h2>
          {data.products.length === 0 ? <Empty text={t("risk.empty")} /> : null}
          {data.products.length ? (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("risk.col.product")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.position")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.incoming")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.demand")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.cover")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.p30")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.p60")}</TableHead>
                    <TableHead>{t("risk.col.orders")}</TableHead>
                    {canAct ? <TableHead /> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.products.map((p) => (
                    <TableRow key={p.product_id} className={p.p_stockout_30 >= 0.5 ? "bg-destructive/5" : ""}>
                      <TableCell>
                        <div className="font-medium">{p.product_ref}</div>
                        <div className="max-w-[18rem] truncate text-xs text-muted-foreground" title={p.product_name}>
                          {p.product_name}
                        </div>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(p.position, locale, 0)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(p.incoming_30, locale, 0)} / {formatNumber(p.incoming_60, locale, 0)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(p.daily_mean, locale, 2)}
                        <span className="text-xs text-muted-foreground"> ±{formatNumber(p.daily_sigma, locale, 2)}</span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{p.days_of_cover === null || p.days_of_cover === undefined ? "—" : t("risk.days", { n: formatNumber(p.days_of_cover, locale, 0) })}</TableCell>
                      <TableCell className="text-right">
                        <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium tabular-nums", tone(p.p_stockout_30))} aria-label={t("risk.col.p30")}>
                          {pct(p.p_stockout_30)}
                        </span>
                      </TableCell>
                      <TableCell className="text-right">
                        <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium tabular-nums", tone(p.p_stockout_60))}>{pct(p.p_stockout_60)}</span>
                      </TableCell>
                      <TableCell className="text-xs">
                        {p.open_po_names.map((po) => (
                          <Link key={po} to="/board" search={{ po }} className={cn("mr-1 underline", p.late_po_names.includes(po) ? "text-destructive" : "text-primary")}>
                            {po}
                            {p.late_po_names.includes(po) ? ` (${t("risk.late")})` : ""}
                          </Link>
                        ))}
                      </TableCell>
                      {canAct ? (
                        <TableCell className="text-right">
                          {p.p_stockout_60 >= 0.2 || p.late_po_names.length ? (
                            // the order behind the risk: the late one first, else the open one
                            <Button
                              size="sm"
                              variant={p.p_stockout_30 >= 0.5 ? "default" : "outline"}
                              disabled={act.isPending}
                              onClick={() => void run(p)}
                              aria-label={t("risk.act_for", { product: p.product_ref })}
                              title={p.open_po_names.length ? t("risk.act_alternate_hint", { po: p.late_po_names[0] ?? p.open_po_names[0] ?? "" }) : t("risk.act_round_hint", { qty: formatNumber(p.suggested_qty, locale, 0) })}
                            >
                              <Zap className="h-4 w-4" /> {p.open_po_names.length ? t("risk.act_alternate") : t("risk.act_round")}
                            </Button>
                          ) : null}
                        </TableCell>
                      ) : null}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : null}
        </section>

        <section className="space-y-2">
          <h2 className="text-base font-semibold">{t("risk.suppliers")}</h2>
          {data.suppliers.length === 0 ? <Empty text={t("risk.suppliers_empty")} /> : null}
          {data.suppliers.length ? (
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("risk.col.supplier")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.open_lines")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.overdue")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.expected_late")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.otif")}</TableHead>
                    <TableHead className="text-right">{t("risk.col.exposure")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.suppliers.map((s) => (
                    <TableRow key={s.partner_id}>
                      <TableCell className="font-medium">{s.partner_name}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.open_lines}</TableCell>
                      <TableCell className={cn("text-right tabular-nums", s.overdue_lines ? "text-destructive" : "")}>{s.overdue_lines}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(s.expected_late_lines, locale, 1)}</TableCell>
                      <TableCell className="text-right tabular-nums">{s.otif === null || s.otif === undefined ? t("risk.no_history") : pct(s.otif)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(s.exposure, locale, 0)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
