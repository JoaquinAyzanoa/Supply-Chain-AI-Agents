/**
 * The sourcing approvals: an award (who gets the order, with the comparison the
 * agent built), a counter-offer (the number we ask, editable within the cap) and
 * a new supplier (an unknown sender who quoted). Each card owns the edits the
 * approver can make and reports them upward.
 */
import { Trophy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { cn, formatNumber } from "@/lib/utils";
import type { AwardPayload, ComparedQuote, OfferPayload, PartnerPayload } from "./types";

/** Money with two decimals always, so 110 reads as 110.00. */
export const fixed2 = (value: number, locale: string) => new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);

// --- award ------------------------------------------------------------------------------

export type LineChoice = Record<string, number>; // product id -> supplier id

/** The recommended split: the best offer per product, as the comparison found it. */
export function recommendedChoice(payload: AwardPayload): LineChoice {
  const out: LineChoice = {};
  for (const award of payload.comparison.line_awards) out[String(award.product_id)] = award.partner_id;
  return out;
}

export function AwardCard({
  payload,
  choice,
  onChoice,
  canAct,
}: {
  payload: AwardPayload;
  choice: LineChoice;
  onChoice: (next: LineChoice) => void;
  canAct: boolean;
}) {
  const { t, locale } = useI18n();
  const comparison = payload.comparison;
  const money = (value: number | null | undefined, currency: string | null | undefined) =>
    value === null || value === undefined ? "—" : `${fixed2(value, locale)} ${currency ?? ""}`.trim();
  const allTo = (partnerId: number) => {
    const next: LineChoice = {};
    for (const line of comparison.basket) {
      const quote = comparison.quotes.find((q) => q.partner_id === partnerId);
      const priced = quote?.lines.find((l) => l.product_id === line.product_id && l.landed_unit !== null && l.landed_unit !== undefined);
      if (priced) next[String(line.product_id)] = partnerId;
    }
    onChoice(next);
  };
  const single = new Set(Object.values(choice)).size === 1 && Object.keys(choice).length === comparison.basket.length ? Object.values(choice)[0] : null;
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{payload.mode === "direct" ? t("approvals.award.direct") : t("approvals.award.round", { id: comparison.round_id })}</Badge>
        <span className="text-xs text-muted-foreground">{t("approvals.award.replied", { replied: comparison.replied, invited: comparison.invited })}</span>
        {comparison.freight_pct ? <span className="text-xs text-muted-foreground">{t("approvals.award.freight", { pct: comparison.freight_pct })}</span> : null}
      </div>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("approvals.award.col.all")}</TableHead>
              <TableHead>{t("approvals.award.col.supplier")}</TableHead>
              <TableHead>{t("approvals.award.col.total")}</TableHead>
              <TableHead>{t("approvals.award.col.lead")}</TableHead>
              <TableHead>{t("approvals.award.col.score")}</TableHead>
              <TableHead>{t("approvals.award.col.why")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {comparison.quotes.map((quote: ComparedQuote) => {
              const selectable = canAct && quote.total !== null && quote.total !== undefined;
              const selected = single === quote.partner_id;
              return (
                <TableRow key={quote.partner_id} className={cn(selected ? "bg-primary/5" : "", quote.recommended ? "font-medium" : "")}>
                  <TableCell>
                    <input
                      type="radio"
                      name="award-winner"
                      aria-label={t("approvals.award.choose", { supplier: quote.partner_name })}
                      checked={selected}
                      disabled={!selectable}
                      onChange={() => allTo(quote.partner_id)}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1">
                      {quote.recommended ? <Trophy className="h-3.5 w-3.5 text-warning-text" aria-label={t("approvals.award.recommended")} /> : null}
                      {quote.partner_name}
                      {quote.po_name ? <span className="text-xs text-muted-foreground"> · {quote.po_name}</span> : null}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {t(`approvals.award.source.${quote.source}`)}
                      {quote.first_time_supplier ? ` · ${t("approvals.award.first_time")}` : ""}
                    </div>
                  </TableCell>
                  <TableCell className="tabular-nums">{money(quote.total, quote.currency)}</TableCell>
                  <TableCell className="tabular-nums">{quote.lead_days === null || quote.lead_days === undefined ? "—" : t("approvals.award.days", { n: quote.lead_days })}</TableCell>
                  <TableCell className="tabular-nums">{quote.score === null || quote.score === undefined ? "—" : Math.round(quote.score)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{quote.reasons.join("; ")}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <div>
        <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("approvals.award.per_line")}</div>
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("approvals.award.col.product")}</TableHead>
                <TableHead>{t("approvals.award.col.qty")}</TableHead>
                <TableHead>{t("approvals.award.col.winner")}</TableHead>
                <TableHead>{t("approvals.award.col.why")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {comparison.basket.map((line) => {
                const offers = comparison.quotes
                  .map((q) => ({ quote: q, priced: q.lines.find((l) => l.product_id === line.product_id && l.landed_unit !== null && l.landed_unit !== undefined) }))
                  .filter((o) => o.priced);
                const recommended = comparison.line_awards.find((a) => a.product_id === line.product_id);
                const value = choice[String(line.product_id)];
                return (
                  <TableRow key={line.product_id}>
                    <TableCell>{line.product}</TableCell>
                    <TableCell className="tabular-nums">{formatNumber(line.qty, locale, 0)}</TableCell>
                    <TableCell>
                      {offers.length ? (
                        <select
                          aria-label={t("approvals.award.line_winner", { product: line.product })}
                          className="h-8 rounded-md border bg-background px-2 text-sm"
                          value={value ?? ""}
                          disabled={!canAct}
                          onChange={(event) => onChoice({ ...choice, [String(line.product_id)]: Number(event.target.value) })}
                        >
                          <option value="">{t("approvals.award.no_winner")}</option>
                          {offers.map((o) => (
                            <option key={o.quote.partner_id} value={o.quote.partner_id}>
                              {o.quote.partner_name} · {money(o.priced?.landed_unit, o.quote.currency)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="text-muted-foreground">{t("approvals.award.no_price")}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{recommended ? recommended.reasons.join("; ") : ""}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>
      {comparison.recommendation ? <p className="whitespace-pre-line rounded-md bg-muted/40 p-3">{comparison.recommendation}</p> : null}
      <p className="text-xs text-muted-foreground">{payload.mode === "direct" ? t("approvals.award.writes_direct") : t("approvals.award.writes")}</p>
    </div>
  );
}

// --- counter-offer ------------------------------------------------------------------------

export function OfferCard({
  payload,
  offered,
  onOffered,
  canAct,
}: {
  payload: OfferPayload;
  offered: string;
  onOffered: (value: string) => void;
  canAct: boolean;
}) {
  const { t, locale } = useI18n();
  const money = (value: number) => `${fixed2(value, locale)} ${payload.currency ?? ""}`.trim();
  const value = Number(offered);
  const outOfBounds = offered !== "" && (Number.isNaN(value) || value < payload.floor_price || value >= payload.current_price);
  return (
    <div className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.offer.product")}</dt>
          <dd>
            {payload.product} × {formatNumber(payload.qty, locale, 0)}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.offer.current")}</dt>
          <dd className="tabular-nums">{money(payload.current_price)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.offer.target")}</dt>
          <dd className="tabular-nums">
            {money(payload.target_price)} <span className="text-xs text-muted-foreground">· {payload.basis}</span>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.offer.floor")}</dt>
          <dd className="tabular-nums">
            {money(payload.floor_price)} <span className="text-xs text-muted-foreground">({t("approvals.offer.cap", { pct: payload.cap_pct })})</span>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.offer.round")}</dt>
          <dd>{t("approvals.offer.round_of", { n: payload.round_no, max: payload.max_rounds })}</dd>
        </div>
      </dl>
      <div className="flex flex-col gap-1">
        <Label htmlFor="offer-price">{t("approvals.offer.offered")}</Label>
        <Input
          id="offer-price"
          type="number"
          step="0.01"
          min={payload.floor_price}
          max={payload.current_price}
          value={offered}
          disabled={!canAct}
          onChange={(event) => onOffered(event.target.value)}
          className="w-40 tabular-nums"
        />
        <span className={cn("text-xs", outOfBounds ? "text-destructive" : "text-muted-foreground")}>
          {t("approvals.offer.bounds", { floor: money(payload.floor_price), current: money(payload.current_price) })}
        </span>
      </div>
      {payload.justification ? <p className="rounded-md bg-muted/40 p-3">{payload.justification}</p> : null}
      <p className="text-xs text-muted-foreground">{t("approvals.offer.writes")}</p>
    </div>
  );
}

// --- new supplier -----------------------------------------------------------------------

export function PartnerCard({
  payload,
  name,
  onName,
  canAct,
}: {
  payload: PartnerPayload;
  name: string;
  onName: (value: string) => void;
  canAct: boolean;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.partner.sender")}</dt>
          <dd className="break-all">{payload.sender_address ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t("approvals.partner.currency")}</dt>
          <dd>{payload.currency ?? "—"}</dd>
        </div>
      </dl>
      <div className="flex flex-col gap-1">
        <Label htmlFor="partner-name">{t("approvals.partner.name")}</Label>
        <Input id="partner-name" value={name} disabled={!canAct} onChange={(event) => onName(event.target.value)} className="max-w-md" />
        <span className="text-xs text-muted-foreground">{t("approvals.partner.name_hint")}</span>
      </div>
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("approvals.partner.col.ref")}</TableHead>
              <TableHead>{t("approvals.partner.col.description")}</TableHead>
              <TableHead>{t("approvals.partner.col.qty")}</TableHead>
              <TableHead>{t("approvals.partner.col.price")}</TableHead>
              <TableHead>{t("approvals.partner.col.lead")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.lines.map((line, index) => (
              <TableRow key={index}>
                <TableCell>{line.product_ref ?? "—"}</TableCell>
                <TableCell>{line.description}</TableCell>
                <TableCell className="tabular-nums">{line.qty === null || line.qty === undefined ? "—" : formatNumber(line.qty, locale, 0)}</TableCell>
                <TableCell className="tabular-nums">{line.unit_price === null || line.unit_price === undefined ? "—" : fixed2(line.unit_price, locale)}</TableCell>
                <TableCell className="tabular-nums">{line.lead_days === null || line.lead_days === undefined ? "—" : t("approvals.award.days", { n: line.lead_days })}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {payload.web_link ? (
        <a href={payload.web_link} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          {t("approvals.link.outlook")}
        </a>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("approvals.partner.writes")}</p>
    </div>
  );
}
