/**
 * The right pane: the card for the approval's kind, the "why" panel with links,
 * and the actions (approve, edit and approve, reject with a reason).
 */
import { ArrowLeft, Check, Pencil, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label, Textarea } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { agentName } from "@/features/cases/labels";
import { useResolveApproval } from "./api";
import { ChangesCard, EmailCard, EscalationCard, PlanCard, type EmailEdits } from "./ApprovalCards";
import {
  changePayload,
  emailPayload,
  escalationPayload,
  kindOf,
  planPayload,
  type Approval,
  type ProposedChange,
} from "./types";

export function ApprovalDetail({ approval, onBack, withChat = true }: { approval: Approval; onBack: () => void; withChat?: boolean }) {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const resolve = useResolveApproval();
  const kind = kindOf(approval);
  const pending = approval.status === "pending";
  const canAct = pending && hasRole("approver");

  // Per-kind editable state, reset when another approval is shown.
  const email = useMemo(() => (kind === "send_email" ? emailPayload.parse(approval.payload) : null), [kind, approval]);
  const changes = useMemo(() => (kind === "po_change" ? changePayload.parse(approval.payload) : null), [kind, approval]);
  const plan = useMemo(() => (kind === "planning_run" ? planPayload.parse(approval.payload) : null), [kind, approval]);
  const escalation = useMemo(
    () => (kind === "escalation" ? escalationPayload.parse(approval.payload) : null),
    [kind, approval],
  );
  const [editing, setEditing] = useState(false);
  const [emailEdits, setEmailEdits] = useState<EmailEdits>({ subject: "", html_body: "" });
  const [accepted, setAccepted] = useState<Set<number>>(new Set());
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEditing(false);
    setRejecting(false);
    setReason("");
    setError(null);
    setEmailEdits({ subject: email?.subject ?? "", html_body: email?.html_body ?? "" });
    setAccepted(new Set((changes?.changes ?? []).filter((c) => !c.needs_review).map((c) => c.po_line_id)));
  }, [approval.id, email, changes]);

  const editedPayload = (): Record<string, unknown> | undefined => {
    if (email) {
      const out: Record<string, string> = {};
      if (emailEdits.subject !== email.subject) out.subject = emailEdits.subject;
      if (emailEdits.html_body !== email.html_body) out.html_body = emailEdits.html_body;
      return Object.keys(out).length ? out : undefined;
    }
    if (changes) return { accepted_line_ids: [...accepted] };
    if (plan) return { accepted_line_ids: plan.lines.map((line) => line.line_id) };
    return undefined;
  };

  const submit = async (status: "approved" | "rejected") => {
    setError(null);
    try {
      await resolve.mutateAsync({
        id: approval.id,
        status,
        reason: status === "rejected" ? reason || null : null,
        edited_payload: status === "approved" ? (editedPayload() ?? null) : null,
      });
      setRejecting(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const toggle = (change: ProposedChange, on: boolean) =>
    setAccepted((prev) => {
      const next = new Set(prev);
      if (on) next.add(change.po_line_id);
      else next.delete(change.po_line_id);
      return next;
    });

  const acceptedCount = accepted.size;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start gap-2 border-b bg-card p-3">
        <Button variant="ghost" size="icon" className="sm:hidden" aria-label={t("approvals.back")} onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{t(`approvals.kind.${approval.kind}`)}</Badge>
            <StatusBadge status={approval.status} />
            {approval.po_name ? <span className="text-sm font-medium">{approval.po_name}</span> : null}
          </div>
          <h2 className="mt-1 text-base font-semibold leading-snug">{approval.summary}</h2>
          <p className="text-xs text-muted-foreground">
            {t("approvals.requested", { agent: agentName(t, approval.requested_by), at: formatDateTime(approval.created_at, locale) })}
            {approval.resolved_by
              ? ` · ${t("approvals.resolved", { who: approval.resolved_by, at: formatDateTime(approval.resolved_at, locale) })}`
              : ""}
          </p>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-3">
        {email ? <EmailCard payload={email} editing={editing} edits={emailEdits} onEdits={setEmailEdits} /> : null}
        {changes ? <ChangesCard payload={changes} accepted={accepted} onToggle={toggle} /> : null}
        {plan ? <PlanCard payload={plan} /> : null}
        {escalation ? <EscalationCard payload={escalation} caseCode={approval.case_code} withChat={withChat} /> : null}
        {kind === "other" ? (
          <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(approval.payload, null, 2)}</pre>
        ) : null}

        <WhyPanel approval={approval} />
        {approval.reason ? (
          <p className="text-sm">
            <span className="text-muted-foreground">{t("approvals.reason")}: </span>
            {approval.reason}
          </p>
        ) : null}
      </div>

      {canAct ? (
        <div className="flex flex-wrap items-center gap-2 border-t bg-card p-3">
          <Button onClick={() => submit("approved")} disabled={resolve.isPending || (changes !== null && acceptedCount === 0)}>
            <Check className="h-4 w-4" />
            {changes ? t("approvals.approve_lines", { n: acceptedCount }) : editing ? t("approvals.approve_edited") : t("approvals.approve")}
          </Button>
          {email ? (
            <Button variant="outline" onClick={() => setEditing((on) => !on)} disabled={resolve.isPending}>
              <Pencil className="h-4 w-4" /> {editing ? t("approvals.stop_editing") : t("approvals.edit")}
            </Button>
          ) : null}
          <Button variant="destructive" onClick={() => setRejecting(true)} disabled={resolve.isPending}>
            <X className="h-4 w-4" /> {t("approvals.reject")}
          </Button>
          {error ? (
            <span className="text-sm text-destructive" role="alert">
              {error}
            </span>
          ) : null}
        </div>
      ) : null}

      <Dialog open={rejecting} onClose={() => setRejecting(false)} title={t("approvals.reject_title")}>
        <div className="flex flex-col gap-3">
          <Label htmlFor="reject-reason">{t("approvals.reject_reason")}</Label>
          <Textarea id="reject-reason" value={reason} onChange={(event) => setReason(event.target.value)} rows={4} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setRejecting(false)}>
              {t("approvals.cancel")}
            </Button>
            <Button variant="destructive" onClick={() => submit("rejected")} disabled={resolve.isPending}>
              {t("approvals.reject_confirm")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

function WhyPanel({ approval }: { approval: Approval }) {
  const { t } = useI18n();
  const links = [
    ["odoo", approval.links.odoo],
    ["order", approval.links.order],
    ["outlook", approval.links.outlook],
    ["trace", approval.links.trace],
  ].filter((pair): pair is [string, string] => Boolean(pair[1]));
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm">
      <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">{t("approvals.why")}</div>
      <p>{approval.why ?? t("approvals.why_unknown")}</p>
      {links.length ? (
        <div className="mt-2 flex flex-wrap gap-3">
          {links.map(([key, href]) => (
            <a key={key} href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
              {t(`approvals.link.${key}`)}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}
