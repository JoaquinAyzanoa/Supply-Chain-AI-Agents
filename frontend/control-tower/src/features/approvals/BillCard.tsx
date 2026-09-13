/**
 * A supplier invoice against the order and the receipts: the verdict, the
 * reasons and the line table the invoice agent produced. Approving a clean
 * one records the draft bill; approving a held one records it anyway.
 */
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import type { BillPayload } from "./types";

const STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  ok: "success",
  price_variance: "warning",
  qty_variance: "warning",
  not_received: "destructive",
  unmatched: "destructive",
};

export function BillCard({ payload }: { payload: BillPayload }) {
  const { t, locale } = useI18n();
  const invoice = payload.invoice;
  const currency = invoice.currency ?? "";
  const money = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${formatNumber(value, locale, 2)} ${currency}`.trim());
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={payload.verdict === "clean" ? "success" : "warning"}>{t(`approvals.bill.verdict.${payload.verdict}`)}</Badge>
        {payload.existing_bill_name ? <Badge variant="secondary">{t("approvals.bill.existing", { bill: payload.existing_bill_name })}</Badge> : null}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
        <Fact label={t("approvals.bill.number")}>{invoice.invoice_number ?? "—"}</Fact>
        <Fact label={t("approvals.bill.date")}>{invoice.invoice_date ? formatDate(invoice.invoice_date, locale) : "—"}</Fact>
        <Fact label={t("approvals.bill.total")}>{money(invoice.total)}</Fact>
        <Fact label={t("approvals.bill.expected")}>{money(payload.expected_subtotal)}</Fact>
      </dl>
      {payload.reasons.length ? (
        <ul className="list-disc pl-5 text-warning-text">
          {payload.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : (
        <p className="text-success-text">{t("approvals.bill.all_ok")}</p>
      )}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("approvals.bill.col.product")}</TableHead>
            <TableHead className="text-right">{t("approvals.bill.col.billed")}</TableHead>
            <TableHead className="text-right">{t("approvals.bill.col.ordered")}</TableHead>
            <TableHead className="text-right">{t("approvals.bill.col.received")}</TableHead>
            <TableHead>{t("approvals.bill.col.check")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {payload.lines.map((line, index) => (
            <TableRow key={`${line.po_line_id ?? "x"}:${index}`}>
              <TableCell className="max-w-[16rem]">
                <div className="truncate" title={line.product ?? line.invoice_description}>
                  {line.product ?? line.invoice_description}
                </div>
                {line.product && line.product !== line.invoice_description ? (
                  <div className="truncate text-xs text-muted-foreground" title={line.invoice_description}>
                    {line.invoice_description}
                  </div>
                ) : null}
              </TableCell>
              <TableCell className="whitespace-nowrap text-right tabular-nums">
                {line.invoice_qty ?? "—"} × {line.invoice_price !== null && line.invoice_price !== undefined ? formatNumber(line.invoice_price, locale, 2) : "—"}
              </TableCell>
              <TableCell className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
                {line.po_qty ?? "—"} × {line.po_price !== null && line.po_price !== undefined ? formatNumber(line.po_price, locale, 2) : "—"}
              </TableCell>
              <TableCell className="text-right tabular-nums text-muted-foreground">{line.received_qty ?? "—"}</TableCell>
              <TableCell>
                <Badge variant={STATUS_VARIANT[line.status] ?? "secondary"}>{t(`approvals.bill.status.${line.status}`)}</Badge>
                {line.note ? <div className="text-xs text-muted-foreground">{line.note}</div> : null}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground">{t("approvals.bill.never_posted")}</p>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}
