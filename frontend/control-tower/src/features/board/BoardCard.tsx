/**
 * One order on the board. The left edge tells the delivery state (green on
 * time, amber due soon, red late); the frame tells what a person owes it
 * (amber: an approval, purple: an escalation, grey: on hold). The body is
 * what a buyer scans: supplier, amount, planned date, the last email and the
 * agents' next step.
 */
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { CheckCircle2, Clock, GripVertical, Mail, MailOpen } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatMoney } from "@/lib/utils";
import { labelFor } from "@/features/cases/labels";
import type { BoardCard } from "./api";

export function OrderCard({
  card,
  draggable,
  onOpen,
}: {
  card: BoardCard;
  draggable: boolean;
  onOpen: () => void;
}) {
  const { t, locale } = useI18n();
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: card.po_name,
    data: { card },
    disabled: !draggable,
  });
  const frame = card.pending_approval
    ? "border-warning ring-1 ring-warning"
    : card.escalated
      ? "border-violet-500 ring-1 ring-violet-500"
      : card.on_hold_until
        ? "border-dashed border-muted-foreground/60"
        : "border-border";
  const edge =
    card.delivery === "late"
      ? "border-l-destructive"
      : card.delivery === "due_soon"
        ? "border-l-warning"
        : card.delivery === "on_time"
          ? "border-l-success"
          : "border-l-transparent";
  return (
    <article
      ref={setNodeRef}
      aria-label={card.po_name}
      style={{ transform: CSS.Translate.toString(transform) }}
      className={cn(
        "rounded-md border border-l-4 bg-card p-2 text-sm shadow-sm",
        frame,
        edge,
        isDragging ? "z-10 opacity-80 shadow-lg" : "",
      )}
    >
      <div className="flex items-start gap-1">
        {draggable ? (
          <button
            type="button"
            className="-ml-1 mt-0.5 cursor-grab touch-none text-muted-foreground"
            aria-label={t("board.drag", { po: card.po_name })}
            {...listeners}
            {...attributes}
          >
            <GripVertical className="h-4 w-4" />
          </button>
        ) : null}
        <button type="button" className="min-w-0 flex-1 text-left" aria-label={t("board.open", { po: card.po_name })} onClick={onOpen}>
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">{card.po_name}</span>
            <DeliveryBadge card={card} />
          </div>
          <div className="truncate text-muted-foreground" title={card.partner_name}>
            {card.partner_name}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
            <span className="tabular-nums">{formatMoney(card.amount_total, card.currency, locale)}</span>
            {card.date_planned ? <span>{t("board.planned", { date: formatDate(card.date_planned, locale) })}</span> : null}
          </div>
          <MailLine card={card} />
          {card.next_action && card.next_action_at ? (
            <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {t("board.next", { what: labelFor(t, "task", card.next_action), date: formatDate(card.next_action_at, locale) })}
            </div>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-1">
            {card.pending_approval ? <Badge variant="warning">{t("board.approval")}</Badge> : null}
            {card.escalated ? <Badge className="border-transparent bg-violet-500/15 text-violet-700 dark:text-violet-300">{t("board.escalated")}</Badge> : null}
            {card.on_hold_until ? <Badge variant="secondary">{t("board.hold", { date: formatDate(card.on_hold_until, locale) })}</Badge> : null}
            {card.column === "confirmed" || card.column === "incoming" ? (
              card.supplier_confirmed ? (
                <Badge variant="success">
                  <CheckCircle2 className="mr-1 h-3 w-3" /> {t("board.supplier_confirmed")}
                </Badge>
              ) : (
                <Badge variant="outline">{t("board.supplier_silent")}</Badge>
              )
            ) : null}
            {card.receipt_status === "partial" ? <Badge variant="secondary">{t("board.partial")}</Badge> : null}
          </div>
        </button>
      </div>
    </article>
  );
}

export function DeliveryBadge({ card }: { card: BoardCard }) {
  const { t } = useI18n();
  if (card.delivery === "late") return <Badge variant="destructive">{t("board.delivery.late", { n: card.days_late })}</Badge>;
  if (card.delivery === "due_soon") return <Badge variant="warning">{t("board.delivery.due_soon")}</Badge>;
  if (card.delivery === "on_time") return <Badge variant="success">{t("board.delivery.on_time")}</Badge>;
  return null;
}

function MailLine({ card }: { card: BoardCard }) {
  const { t, locale } = useI18n();
  if (card.days_silent !== null && card.days_silent !== undefined && card.days_silent > 0 && !card.last_inbound) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-warning-text">
        <Mail className="h-3 w-3" /> {t("board.silent", { n: card.days_silent })}
      </div>
    );
  }
  if (card.last_inbound) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
        <MailOpen className="h-3 w-3" /> {t("board.last_in", { date: formatDate(card.last_inbound, locale) })}
      </div>
    );
  }
  if (card.last_outbound) {
    return (
      <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
        <Mail className="h-3 w-3" /> {t("board.last_out", { date: formatDate(card.last_outbound, locale) })}
      </div>
    );
  }
  return null;
}
