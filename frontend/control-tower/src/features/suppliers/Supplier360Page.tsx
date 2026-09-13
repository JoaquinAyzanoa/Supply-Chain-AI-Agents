/**
 * Supplier 360: one supplier, everything the desk knows. The scorecard, the open orders,
 * the prices per product and where the supplier ranks for them, the quote rounds, the
 * profile people edit, and the email timeline (dates and links only, never text).
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, Mail } from "lucide-react";

import { api, unwrap, type Schemas } from "@/api/client";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RoundsPanel } from "@/features/sourcing/RoundsPanel";
import { useI18n } from "@/i18n";
import { formatDate, formatDateTime, formatMoney, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { SupplierProfileEditor } from "./SupplierProfileEditor";

export type Supplier360 = Schemas["Supplier360"];

export function useSupplier(partnerId: number) {
  return useQuery({
    queryKey: ["suppliers", partnerId],
    queryFn: async () => unwrap(await api.GET("/api/suppliers/{partner_id}", { params: { path: { partner_id: partnerId } } })) as Supplier360,
  });
}

export function Supplier360Page() {
  const { partnerId } = useParams({ strict: false }) as { partnerId: string };
  const id = Number(partnerId);
  const { t, locale } = useI18n();
  const supplier = useSupplier(id);
  if (supplier.isPending) return <Loading />;
  if (supplier.error) return <ErrorBox error={supplier.error} onRetry={() => supplier.refetch()} />;
  const data = supplier.data;
  const score = data.score as Record<string, unknown> | null;
  const num = (key: string): number | null => (score && typeof score[key] === "number" ? (score[key] as number) : null);
  return (
    <div>
      <PageTitle title={data.partner_name}>
        <Link to="/suppliers" className="inline-flex items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft className="h-3 w-3" /> {t("supplier.all")}
        </Link>
      </PageTitle>
      <div className="space-y-6 p-4">
        <section className="grid gap-3 sm:grid-cols-4" aria-label={t("supplier.scorecard")}>
          <Stat label={t("supplier.score")} value={num("score") === null ? "—" : formatNumber(num("score")!, locale, 0)} tone={num("score") === null ? "" : num("score")! >= 80 ? "text-success-text" : num("score")! >= 60 ? "text-warning-text" : "text-destructive"} />
          <Stat label={t("supplier.otif")} value={num("otif") === null ? "—" : `${Math.round(num("otif")! * 100)}%`} />
          <Stat label={t("supplier.lead")} value={num("lead_time_mean_days") === null ? "—" : t("risk.days", { n: formatNumber(num("lead_time_mean_days")!, locale, 0) })} />
          <Stat label={t("supplier.response")} value={num("response_hours_median") === null ? "—" : `${formatNumber(num("response_hours_median")!, locale, 1)} h`} />
          {score && typeof score.scorecard === "string" ? <p className="text-sm text-muted-foreground sm:col-span-4">{score.scorecard}</p> : null}
          {!score ? <p className="text-sm text-muted-foreground sm:col-span-4">{t("supplier.no_scorecard")}</p> : null}
        </section>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-lg border bg-card p-3" aria-label={t("supplier.orders")}>
            <h2 className="mb-2 text-sm font-semibold">{t("supplier.orders")}</h2>
            {data.orders.length === 0 ? (
              <Empty text={t("supplier.no_orders")} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("supplier.col.order")}</TableHead>
                    <TableHead>{t("supplier.col.state")}</TableHead>
                    <TableHead>{t("supplier.col.planned")}</TableHead>
                    <TableHead className="text-right">{t("supplier.col.amount")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.orders.map((o) => (
                    <TableRow key={o.po_id}>
                      <TableCell>
                        <Link to="/board" search={{ po: o.po_name }} className="font-medium text-primary underline">
                          {o.po_name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={o.state} />
                      </TableCell>
                      <TableCell>{o.date_planned ? formatDate(o.date_planned, locale) : "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatMoney(o.amount_total, o.currency, locale)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </section>
          <section className="rounded-lg border bg-card p-3" aria-label={t("supplier.prices")}>
            <h2 className="mb-2 text-sm font-semibold">{t("supplier.prices")}</h2>
            {data.prices.length === 0 ? (
              <Empty text={t("supplier.no_prices")} />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("supplier.col.product")}</TableHead>
                    <TableHead className="text-right">{t("supplier.col.price")}</TableHead>
                    <TableHead className="text-right">{t("supplier.col.min")}</TableHead>
                    <TableHead className="text-right">{t("supplier.col.lead")}</TableHead>
                    <TableHead className="text-right">{t("supplier.col.rank")}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.prices.map((p, index) => {
                    const rank = data.products.find((r) => r.product_id === p.product_id);
                    return (
                      <TableRow key={index}>
                        <TableCell className="max-w-[16rem] truncate" title={p.product}>
                          {p.product}
                          {p.supplier_code ? <span className="ml-1 text-xs text-muted-foreground">{p.supplier_code}</span> : null}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{formatMoney(p.price, p.currency, locale)}</TableCell>
                        <TableCell className="text-right tabular-nums">{formatNumber(p.min_qty, locale, 0)}</TableCell>
                        <TableCell className="text-right tabular-nums">{t("risk.days", { n: p.lead_days })}</TableCell>
                        <TableCell className="text-right">
                          {rank && rank.rank !== null && rank.rank !== undefined ? (
                            <Badge variant={rank.rank === 1 ? "success" : "secondary"} title={rank.best_partner_name ? t("supplier.best", { name: rank.best_partner_name }) : undefined}>
                              {t("supplier.rank_of", { rank: rank.rank, n: rank.suppliers })}
                            </Badge>
                          ) : (
                            "—"
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </section>
        </div>

        <section aria-label={t("supplier.rounds")}>
          <h2 className="mb-2 text-sm font-semibold">{t("supplier.rounds")}</h2>
          <RoundsPanel partnerId={id} />
        </section>

        <section className="rounded-lg border bg-card p-3" aria-label={t("supplier.profile")}>
          <h2 className="mb-2 text-sm font-semibold">{t("supplier.profile")}</h2>
          <SupplierProfileEditor partnerId={id} />
        </section>

        <section className="rounded-lg border bg-card p-3" aria-label={t("supplier.emails")}>
          <h2 className="mb-2 text-sm font-semibold">{t("supplier.emails")}</h2>
          <p className="mb-2 text-xs text-muted-foreground">{t("supplier.emails_hint")}</p>
          {data.emails.length === 0 ? (
            <Empty text={t("supplier.no_emails")} />
          ) : (
            <ul className="space-y-1 text-sm">
              {data.emails.map((m, index) => (
                <li key={index} className="flex flex-wrap items-center gap-2">
                  <Mail className="h-3 w-3 text-muted-foreground" />
                  <Badge variant={m.direction === "in" ? "secondary" : "outline"}>{t(`supplier.direction.${m.direction}`)}</Badge>
                  <span className="font-medium">{m.po_name}</span>
                  <span className="text-muted-foreground">{m.at ? formatDateTime(m.at, locale) : "—"}</span>
                  {m.web_link ? (
                    <a href={m.web_link} target="_blank" rel="noopener noreferrer" className="text-primary underline">
                      {t("approvals.link.outlook")}
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}
