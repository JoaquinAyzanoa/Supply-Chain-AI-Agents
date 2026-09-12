/**
 * One card body per approval kind. Each card owns the edits the approver can
 * make and reports them upward as the `edited_payload` the API expects
 * (EmailEdits, ChangeEdits or PlanEdits).
 */
import { Link } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import { EmailPreview } from "./EmailPreview";
import type { ChangePayload, EmailPayload, EscalationPayload, PlanPayload, ProposedChange } from "./types";

// --- send_email -------------------------------------------------------------------------

export interface EmailEdits {
  subject: string;
  html_body: string;
}

export function EmailCard({
  payload,
  editing,
  edits,
  onEdits,
}: {
  payload: EmailPayload;
  editing: boolean;
  edits: EmailEdits;
  onEdits: (edits: EmailEdits) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">{t("approvals.email.to")}</dt>
        <dd className="break-all">{payload.to.join(", ")}</dd>
        <dt className="text-muted-foreground">{t("approvals.email.subject")}</dt>
        <dd>
          {editing ? (
            <Input
              aria-label={t("approvals.email.subject")}
              value={edits.subject}
              onChange={(event) => onEdits({ ...edits, subject: event.target.value })}
            />
          ) : (
            edits.subject
          )}
        </dd>
        {payload.attachments.length ? (
          <>
            <dt className="text-muted-foreground">{t("approvals.email.attachments")}</dt>
            <dd>{payload.attachments.join(", ")}</dd>
          </>
        ) : null}
      </dl>
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email-body">{t("approvals.email.body")}</Label>
          <Textarea
            id="email-body"
            rows={14}
            className="font-mono text-xs"
            value={edits.html_body}
            onChange={(event) => onEdits({ ...edits, html_body: event.target.value })}
          />
        </div>
      ) : null}
      <EmailPreview html={edits.html_body} />
    </div>
  );
}

// --- po_change --------------------------------------------------------------------------

export function ChangesCard({
  payload,
  accepted,
  onToggle,
}: {
  payload: ChangePayload;
  accepted: Set<number>;
  onToggle: (change: ProposedChange, on: boolean) => void;
}) {
  const { t, locale } = useI18n();
  return (
    <div className="flex flex-col gap-3">
      {payload.summary ? <p className="text-sm">{payload.summary}</p> : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8" />
            <TableHead>{t("approvals.changes.product")}</TableHead>
            <TableHead>{t("approvals.changes.field")}</TableHead>
            <TableHead>{t("approvals.changes.before")}</TableHead>
            <TableHead>{t("approvals.changes.after")}</TableHead>
            <TableHead>{t("approvals.changes.source")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {payload.changes.map((change) => {
            const on = accepted.has(change.po_line_id) && !change.needs_review;
            return (
              <TableRow key={`${change.po_line_id}:${change.field}`} className={change.needs_review ? "opacity-70" : ""}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`${change.product} ${change.field}`}
                    checked={on}
                    disabled={change.needs_review}
                    onChange={(event) => onToggle(change, event.target.checked)}
                  />
                </TableCell>
                <TableCell className="max-w-[16rem] truncate" title={change.product}>
                  {change.product}
                </TableCell>
                <TableCell>{t(`approvals.changes.field.${change.field}`)}</TableCell>
                <TableCell className="text-muted-foreground">{formatValue(change.field, change.before, locale)}</TableCell>
                <TableCell className="font-medium">{formatValue(change.field, change.after, locale)}</TableCell>
                <TableCell>
                  <span className="flex items-center gap-1">
                    {change.source ?? ""}
                    {change.confidence !== null && change.confidence !== undefined ? (
                      <Badge variant={change.confidence >= 0.8 ? "success" : "warning"}>
                        {Math.round(change.confidence * 100)}%
                      </Badge>
                    ) : null}
                  </span>
                  {change.needs_review ? (
                    <div className="text-xs text-muted-foreground">{change.review_reason ?? t("approvals.changes.review")}</div>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function formatValue(field: string, value: string | number | null | undefined, locale: string): string {
  if (value === null || value === undefined) return "—";
  if (field === "date_planned" && typeof value === "string") return formatDate(value, locale);
  if (typeof value === "number") return formatNumber(value, locale, 4);
  return value;
}

// --- planning_run -----------------------------------------------------------------------

export function PlanCard({ payload }: { payload: PlanPayload }) {
  const { t, locale } = useI18n();
  const totals = payload.totals;
  return (
    <div className="flex flex-col gap-3 text-sm">
      {payload.summary ? <p className="whitespace-pre-line">{payload.summary}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Badge variant="secondary">{t("approvals.plan.lines", { n: totals.lines ?? payload.lines.length })}</Badge>
        <Badge variant="secondary">{t("approvals.plan.rfqs", { n: totals.rfq_lines ?? 0 })}</Badge>
        <Badge variant="secondary">{t("approvals.plan.rules", { n: totals.rules_changed ?? 0 })}</Badge>
        <Badge variant={totals.exceptions ? "warning" : "secondary"}>
          {t("approvals.plan.exceptions", { n: totals.exceptions ?? payload.exceptions.length })}
        </Badge>
      </div>
      <Link to="/planning/$runId" params={{ runId: payload.run_id }} className="inline-flex items-center gap-1 text-primary underline">
        {t("approvals.plan.review")} <ExternalLink className="h-3 w-3" />
      </Link>
      {payload.exceptions.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("approvals.plan.product")}</TableHead>
              <TableHead>{t("approvals.plan.exception")}</TableHead>
              <TableHead>{t("approvals.plan.action")}</TableHead>
              <TableHead className="text-right">{t("approvals.plan.qty")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {payload.exceptions.map((line) => (
              <TableRow key={line.line_id}>
                <TableCell>{line.product_ref}</TableCell>
                <TableCell>
                  <Badge variant="warning">{line.exception ?? ""}</Badge>
                  {line.explanation ? <div className="text-xs text-muted-foreground">{line.explanation}</div> : null}
                </TableCell>
                <TableCell>{line.action ?? ""}</TableCell>
                <TableCell className="text-right">{formatNumber(line.order_qty, locale)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}

// --- escalation -------------------------------------------------------------------------

export function EscalationCard({ payload }: { payload: EscalationPayload }) {
  const { t } = useI18n();
  const details = Object.entries(payload.details).filter(([, v]) => v !== null && v !== "" && v !== undefined);
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p className="whitespace-pre-line">{payload.reason}</p>
      {details.length ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          {details.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-muted-foreground">{key}</dt>
              <dd>{typeof value === "string" ? value : JSON.stringify(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {payload.history.length ? (
        <div>
          <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("approvals.escalation.history")}</div>
          <ol className="list-decimal pl-5 text-xs text-muted-foreground">
            {payload.history.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ol>
        </div>
      ) : null}
      {payload.case_id ? (
        <Link to="/cases/$caseId" params={{ caseId: payload.case_id }} className="text-primary underline">
          {t("approvals.escalation.case")}
        </Link>
      ) : null}
    </div>
  );
}
