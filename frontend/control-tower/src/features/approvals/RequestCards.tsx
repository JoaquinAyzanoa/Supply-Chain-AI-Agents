/**
 * Phase 11 S6 approval cards: an employee's purchase request (which items to order,
 * from whom) and a supplier's price list (which rows to record). The approver ticks
 * rows; unmatched rows can never be ticked because nothing in Odoo answers to them.
 */
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import { fixed2 } from "./SourcingCards";
import type { PriceListPayload, RequestPayload } from "./types";

export function InternalRequestCard({
  payload,
  accepted,
  onToggle,
  canAct,
}: {
  payload: RequestPayload;
  accepted: Set<number>;
  onToggle: (index: number, on: boolean) => void;
  canAct: boolean;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.request.sender")}</dt>
          <dd className="break-all">{payload.sender_address ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.request.need_date")}</dt>
          <dd>
            {payload.need_date ? formatDate(payload.need_date, locale) : "—"}
            {payload.need_date_raw ? <span className="text-xs text-muted-foreground"> ({payload.need_date_raw})</span> : null}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.request.confidence")}</dt>
          <dd>
            <Badge variant={payload.confidence >= 0.8 ? "success" : "warning"}>{Math.round(payload.confidence * 100)}%</Badge>
          </dd>
        </div>
      </dl>
      {payload.notes ? <p className="text-sm">{payload.notes}</p> : null}
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>{t("approvals.request.col.asked")}</TableHead>
              <TableHead>{t("approvals.request.col.product")}</TableHead>
              <TableHead className="text-right">{t("approvals.request.col.qty")}</TableHead>
              <TableHead>{t("approvals.request.col.supplier")}</TableHead>
              <TableHead className="text-right">{t("approvals.request.col.price")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.items.map((item, index) => {
              const matched = item.product_id !== null && item.product_id !== undefined;
              const orderable = matched && item.supplier_id !== null && item.supplier_id !== undefined;
              return (
                <TableRow key={index} className={orderable ? "" : "opacity-70"}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={t("approvals.request.order_item", { item: item.description })}
                      checked={orderable && accepted.has(index)}
                      disabled={!orderable || !canAct}
                      onChange={(event) => onToggle(index, event.target.checked)}
                    />
                  </TableCell>
                  <TableCell className="max-w-[14rem] truncate" title={item.description}>
                    {item.description}
                    {item.product_ref ? <span className="ml-1 text-xs text-muted-foreground">{item.product_ref}</span> : null}
                  </TableCell>
                  <TableCell className="max-w-[16rem] truncate" title={item.product ?? ""}>
                    {matched ? (
                      <span className="flex items-center gap-1">
                        {item.product}
                        <Badge variant={item.match_confidence >= 0.95 ? "success" : "warning"}>{Math.round(item.match_confidence * 100)}%</Badge>
                      </span>
                    ) : (
                      <span className="text-muted-foreground">{t("approvals.request.not_in_catalogue")}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatNumber(item.qty, locale, 0)} {item.uom ?? ""}
                  </TableCell>
                  <TableCell>{item.supplier_name ?? (matched ? <span className="text-muted-foreground">{t("approvals.request.no_supplier")}</span> : "—")}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {item.unit_price === null || item.unit_price === undefined ? "—" : `${fixed2(item.unit_price, locale)} ${item.currency ?? ""}`}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {payload.web_link ? (
        <a href={payload.web_link} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          {t("approvals.link.outlook")}
        </a>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("approvals.request.writes")}</p>
    </div>
  );
}

export function PriceListCard({
  payload,
  accepted,
  onToggle,
  canAct,
}: {
  payload: PriceListPayload;
  accepted: Set<string>;
  onToggle: (code: string, on: boolean) => void;
  canAct: boolean;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p>
        {t("approvals.pricelist.intro", { partner: payload.partner_name || String(payload.partner_id), source: payload.source, changed: payload.changed, unmatched: payload.unmatched })}
      </p>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>{t("approvals.pricelist.col.code")}</TableHead>
              <TableHead>{t("approvals.pricelist.col.product")}</TableHead>
              <TableHead className="text-right">{t("approvals.pricelist.col.current")}</TableHead>
              <TableHead className="text-right">{t("approvals.pricelist.col.new")}</TableHead>
              <TableHead className="text-right">{t("approvals.pricelist.col.change")}</TableHead>
              <TableHead className="text-right">{t("approvals.pricelist.col.terms")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.rows.map((row) => {
              const same = row.matched && row.current_price !== null && row.current_price !== undefined && Math.abs(row.current_price - row.new_price) < 0.005;
              return (
                <TableRow key={row.code} className={row.matched ? "" : "opacity-70"}>
                  <TableCell>
                    <input
                      type="checkbox"
                      aria-label={t("approvals.pricelist.record_row", { code: row.code })}
                      checked={row.matched && accepted.has(row.code)}
                      disabled={!row.matched || !canAct}
                      onChange={(event) => onToggle(row.code, event.target.checked)}
                    />
                  </TableCell>
                  <TableCell className="font-medium">{row.code}</TableCell>
                  <TableCell className="max-w-[16rem] truncate" title={row.product ?? row.description}>
                    {row.matched ? row.product : <span className="text-muted-foreground">{t("approvals.request.not_in_catalogue")}</span>}
                    {!row.matched && row.description ? <div className="text-xs text-muted-foreground">{row.description}</div> : null}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{row.current_price === null || row.current_price === undefined ? t("approvals.pricelist.new_product") : fixed2(row.current_price, locale)}</TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    {fixed2(row.new_price, locale)} {row.currency ?? payload.currency ?? ""}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.change_pct === null || row.change_pct === undefined ? (
                      "—"
                    ) : same ? (
                      <span className="text-muted-foreground">{t("approvals.pricelist.same")}</span>
                    ) : (
                      <Badge variant={row.change_pct > 0 ? "warning" : "success"}>
                        {row.change_pct > 0 ? "+" : ""}
                        {formatNumber(row.change_pct, locale, 1)}%
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                    {row.min_qty ? t("approvals.pricelist.min_qty", { n: formatNumber(row.min_qty, locale, 0) }) : ""}
                    {row.min_qty && row.lead_days !== null && row.lead_days !== undefined ? " · " : ""}
                    {row.lead_days !== null && row.lead_days !== undefined ? t("approvals.award.days", { n: row.lead_days }) : ""}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {payload.web_link ? (
        <a href={payload.web_link} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          {t("approvals.link.outlook")}
        </a>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("approvals.pricelist.writes")}</p>
    </div>
  );
}
