/**
 * The side panel for one order: the facts, the moves an approver may make
 * (with a note), the pending approval resolvable in place, the case history
 * and the conversation with the director. Everything the inbox, the cases
 * screen and the exceptions board used to spread over three pages.
 */
import { Link } from "@tanstack/react-router";
import { CheckCircle2, ExternalLink, X } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Label, Textarea } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { formatDate, formatMoney } from "@/lib/utils";
import { ApprovalDetail } from "@/features/approvals/ApprovalDetail";
import { useApproval } from "@/features/approvals/api";
import { CaseEvents } from "@/features/cases/CaseTimeline";
import { useCase } from "@/features/cases/api";
import { CaseChat } from "@/features/chat/CaseChat";
import { targetsFor, useMoveCard, useSupplierConfirmed, type BoardCard, type Column } from "./api";
import { DeliveryBadge } from "./BoardCard";

export function OrderDrawer({ card, onClose }: { card: BoardCard; onClose: () => void }) {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const canAct = hasRole("approver");
  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-label={t("board.drawer.order", { po: card.po_name })}
      className="fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l bg-background shadow-xl sm:w-[34rem] lg:w-[40rem]"
    >
      <header className="flex items-start gap-2 border-b bg-card p-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{card.po_name}</h2>
            <Badge variant="outline">{t(`board.col.${card.column}`)}</Badge>
            <DeliveryBadge card={card} />
            {card.pending_approval ? <Badge variant="warning">{t("board.approval")}</Badge> : null}
            {card.escalated ? <Badge className="border-transparent bg-violet-500/15 text-violet-700 dark:text-violet-300">{t("board.escalated")}</Badge> : null}
          </div>
          {card.summary ? <p className="mt-1 text-sm">{card.summary}</p> : null}
        </div>
        <Button variant="ghost" size="icon" aria-label={t("board.drawer.close")} onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
          <Fact label={t("board.drawer.supplier")}>{card.partner_name}</Fact>
          <Fact label={t("board.drawer.amount")}>{formatMoney(card.amount_total, card.currency, locale)}</Fact>
          <Fact label={t("board.drawer.planned")}>
            {card.date_planned ? formatDate(card.date_planned, locale) : "—"}
            {card.eta_source ? <span className="text-muted-foreground"> · {t(`board.eta.${card.eta_source}`)}</span> : null}
          </Fact>
          {card.buyer ? <Fact label={t("board.drawer.buyer")}>{card.buyer}</Fact> : null}
          {card.last_outbound ? <Fact label={t("board.drawer.last_out")}>{formatDate(card.last_outbound, locale)}</Fact> : null}
          {card.last_inbound ? <Fact label={t("board.drawer.last_in")}>{formatDate(card.last_inbound, locale)}</Fact> : null}
          {card.on_hold_until ? <Fact label={t("board.drawer.hold")}>{formatDate(card.on_hold_until, locale)}</Fact> : null}
        </dl>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <a href={card.odoo_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-primary underline">
            {t("board.drawer.odoo")} <ExternalLink className="h-3 w-3" />
          </a>
          {card.case_code ? (
            <Link to="/cases/$caseId" params={{ caseId: card.case_code }} className="text-primary underline">
              {t("board.drawer.case_page", { code: card.case_code })}
            </Link>
          ) : null}
        </div>

        {canAct ? <Moves card={card} /> : <p className="text-xs text-muted-foreground">{t("board.role_hint")}</p>}

        {card.pending_approval ? <PendingApproval id={card.pending_approval.id} onBack={onClose} /> : null}

        {card.case_id ? (
          <>
            <CaseChat caseRef={card.case_code ?? card.case_id} compact />
            <CaseHistory caseId={card.case_id} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{t("board.drawer.no_case")}</p>
        )}
      </div>
    </aside>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** The moves Odoo allows from where the card is, plus the manual supplier mark. */
function Moves({ card }: { card: BoardCard }) {
  const { t } = useI18n();
  const move = useMoveCard();
  const mark = useSupplierConfirmed();
  const [closing, setClosing] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const targets = targetsFor(card);
  const run = async (to: Column, withNote?: string) => {
    setError(null);
    setMessage(null);
    try {
      const result = await move.mutateAsync({ po_name: card.po_name, to, note: withNote });
      setMessage(result.message);
      setClosing(false);
      setNote("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };
  const showMark = card.column === "confirmed" || card.column === "incoming";
  if (targets.length === 0 && !showMark) return null;
  return (
    <section aria-label={t("board.move")} className="rounded-md border bg-muted/30 p-3">
      <h3 className="mb-2 text-sm font-semibold">{t("board.move")}</h3>
      <div className="flex flex-wrap gap-2">
        {targets.map((to) =>
          to === "closed" ? (
            <Button key={to} variant="outline" size="sm" onClick={() => setClosing(true)} disabled={move.isPending}>
              {card.state === "purchase" ? t("board.move.closed") : t("board.move.cancel")}
            </Button>
          ) : (
            <Button key={to} size="sm" variant={to === "confirmed" ? "default" : "secondary"} onClick={() => run(to)} disabled={move.isPending}>
              {t(`board.move.${to}`)}
            </Button>
          ),
        )}
        {showMark ? (
          <Button
            variant="outline"
            size="sm"
            disabled={mark.isPending}
            onClick={async () => {
              setError(null);
              try {
                const result = await mark.mutateAsync({ po_name: card.po_name, value: !card.supplier_confirmed });
                setMessage(result.message);
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : String(exc));
              }
            }}
          >
            <CheckCircle2 className="h-4 w-4" />
            {card.supplier_confirmed ? t("board.unmark_confirmed") : t("board.mark_confirmed")}
          </Button>
        ) : null}
      </div>
      {showMark && card.receipt_status !== "full" ? <p className="mt-2 text-xs text-muted-foreground">{t("board.move.receive_in_odoo")}</p> : null}
      {message ? (
        <p role="status" className="mt-2 text-xs text-success-text">
          {message}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <Dialog open={closing} onClose={() => setClosing(false)} title={card.state === "purchase" ? t("board.move.closed") : t("board.move.cancel")}>
        <Label htmlFor="board-close-note">{t("board.move.note")}</Label>
        <Textarea id="board-close-note" value={note} onChange={(event) => setNote(event.target.value)} rows={3} />
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setClosing(false)}>
            {t("approvals.cancel")}
          </Button>
          <Button variant="destructive" onClick={() => run("closed", note)} disabled={move.isPending}>
            {t("board.move.confirm")}
          </Button>
        </div>
      </Dialog>
    </section>
  );
}

function PendingApproval({ id, onBack }: { id: number; onBack: () => void }) {
  const { t } = useI18n();
  const approval = useApproval(id);
  return (
    <section aria-label={t("board.drawer.approval")} className="rounded-md border border-warning">
      <h3 className="border-b bg-warning/10 px-3 py-2 text-sm font-semibold">{t("board.drawer.approval")}</h3>
      {approval.isPending ? <Loading /> : null}
      {approval.error ? <ErrorBox error={approval.error} onRetry={() => approval.refetch()} /> : null}
      {approval.data ? <ApprovalDetail approval={approval.data} onBack={onBack} /> : null}
    </section>
  );
}

function CaseHistory({ caseId }: { caseId: string }) {
  const { t } = useI18n();
  const detail = useCase(caseId);
  return (
    <section aria-label={t("board.drawer.case")}>
      <h3 className="mb-2 text-sm font-semibold">{t("board.drawer.case")}</h3>
      {detail.isPending ? <Loading /> : null}
      {detail.error ? <ErrorBox error={detail.error} onRetry={() => detail.refetch()} /> : null}
      {detail.data ? <CaseEvents events={detail.data.events} /> : null}
    </section>
  );
}
