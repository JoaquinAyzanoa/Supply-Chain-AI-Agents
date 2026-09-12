/**
 * The landing page: every purchase order as a card in the column its life is
 * at, from proposal to closed. Approvers drag cards where Odoo allows (send a
 * proposal, confirm an RFQ, close an order); everything else moves by itself
 * as emails and receipts arrive. Filters and the open card live in the URL.
 */
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { CalendarClock, MailCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { cn, formatDate } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { COLUMNS, hasProblem, targetsFor, useBoard, useCheckMailbox, useMoveCard, type BoardCard, type Column } from "./api";
import { OrderCard } from "./BoardCard";
import { OrderDrawer } from "./OrderDrawer";

export interface BoardSearch {
  po?: string;
  q?: string;
  supplier?: string;
  buyer?: string;
  problems?: string;
}

export function BoardPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as BoardSearch;
  const board = useBoard();
  const move = useMoveCard();
  const mailbox = useCheckMailbox();
  const [closing, setClosing] = useState<BoardCard | null>(null);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor));
  const canDrag = hasRole("approver");

  const setSearch = (patch: Partial<BoardSearch>) =>
    void navigate({ to: "/", search: (prev: BoardSearch) => clean({ ...prev, ...patch }) });

  const cards = board.data?.cards ?? [];
  const suppliers = useMemo(() => unique(cards.map((c) => c.partner_name)), [cards]);
  const buyers = useMemo(() => unique(cards.map((c) => c.buyer ?? "")), [cards]);
  const visible = cards.filter((card) => {
    if (search.supplier && card.partner_name !== search.supplier) return false;
    if (search.buyer && (card.buyer ?? "") !== search.buyer) return false;
    if (search.problems && !hasProblem(card)) return false;
    if (search.q) {
      const needle = search.q.toLowerCase();
      if (!card.po_name.toLowerCase().includes(needle) && !card.partner_name.toLowerCase().includes(needle)) return false;
    }
    return true;
  });
  const open = search.po ? cards.find((c) => c.po_name === search.po) : undefined;

  const perform = async (card: BoardCard, to: Column, withNote?: string) => {
    try {
      const result = await move.mutateAsync({ po_name: card.po_name, to, note: withNote });
      setNotice({ kind: "ok", text: result.message });
    } catch (exc) {
      setNotice({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
    }
  };

  const onDragEnd = (event: DragEndEvent) => {
    const card = event.active.data.current?.card as BoardCard | undefined;
    const to = event.over?.id as Column | undefined;
    if (!card || !to || to === card.column) return;
    if (!targetsFor(card).includes(to)) {
      setNotice({ kind: "error", text: to === "received" ? t("board.move.receive_in_odoo") : t("board.move.not_allowed") });
      return;
    }
    if (to === "closed") {
      setNote("");
      setClosing(card);
      return;
    }
    void perform(card, to);
  };

  return (
    <div className="flex h-screen flex-col">
      <PageTitle title={t("board.title")}>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={t("board.search")}
            placeholder={t("board.search")}
            className="h-8 w-44"
            defaultValue={search.q ?? ""}
            onChange={(event) => setSearch({ q: event.target.value || undefined })}
          />
          <select
            aria-label={t("board.filter.supplier")}
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.supplier ?? ""}
            onChange={(event) => setSearch({ supplier: event.target.value || undefined })}
          >
            <option value="">{t("board.filter.all_suppliers")}</option>
            {suppliers.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          {buyers.length > 1 ? (
            <select
              aria-label={t("board.filter.buyer")}
              className="h-8 rounded-md border bg-card px-2 text-sm"
              value={search.buyer ?? ""}
              onChange={(event) => setSearch({ buyer: event.target.value || undefined })}
            >
              <option value="">{t("board.filter.all_buyers")}</option>
              {buyers.map((name) => (
                <option key={name} value={name}>
                  {name || "—"}
                </option>
              ))}
            </select>
          ) : null}
          <label className="flex items-center gap-1 text-sm">
            <input type="checkbox" checked={Boolean(search.problems)} onChange={(event) => setSearch({ problems: event.target.checked ? "1" : undefined })} />
            {t("board.filter.problems")}
          </label>
          {canDrag ? (
            <Button
              variant="outline"
              size="sm"
              disabled={mailbox.isPending}
              onClick={async () => {
                try {
                  const report = await mailbox.mutateAsync();
                  setNotice({ kind: "ok", text: t("board.mailbox.result", { what: report.message }) });
                } catch (exc) {
                  setNotice({ kind: "error", text: exc instanceof Error ? exc.message : String(exc) });
                }
              }}
            >
              <MailCheck className="h-4 w-4" />
              {mailbox.isPending ? t("board.mailbox.reading") : t("board.mailbox.check")}
            </Button>
          ) : null}
          {board.data ? <span className="text-xs text-muted-foreground">{t("board.as_of", { date: formatDate(board.data.as_of, locale) })}</span> : null}
        </div>
      </PageTitle>
      {notice ? (
        <p role={notice.kind === "ok" ? "status" : "alert"} className={cn("border-b px-4 py-1.5 text-sm", notice.kind === "ok" ? "bg-success/10 text-success-text" : "bg-destructive/10 text-destructive")}>
          {notice.text}
        </p>
      ) : null}
      {board.data?.planning ? (
        <p className="flex flex-wrap items-center gap-2 border-b bg-accent/60 px-4 py-1.5 text-sm" role="status">
          <CalendarClock className="h-4 w-4" />
          {t("board.planning", { date: board.data.planning.as_of ? formatDate(board.data.planning.as_of, locale) : "", summary: board.data.planning.summary })}
          {board.data.planning.run_id ? (
            <Link to="/planning/$runId" params={{ runId: board.data.planning.run_id }} className="font-medium text-primary underline">
              {t("board.planning.review")}
            </Link>
          ) : (
            <Link to="/approvals" search={{ id: board.data.planning.approval_id }} className="font-medium text-primary underline">
              {t("board.planning.review")}
            </Link>
          )}
        </p>
      ) : null}
      {board.isPending ? <Loading /> : null}
      {board.error ? <ErrorBox error={board.error} onRetry={() => board.refetch()} /> : null}
      {board.data ? (
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto p-4">
            {COLUMNS.map((column) => (
              <BoardColumn
                key={column}
                column={column}
                cards={visible.filter((c) => c.column === column)}
                total={board.data.counts[column] ?? 0}
                canDrag={canDrag}
                onOpen={(card) => setSearch({ po: card.po_name })}
              />
            ))}
          </div>
        </DndContext>
      ) : null}
      {open ? <OrderDrawer card={open} onClose={() => setSearch({ po: undefined })} /> : null}
      <Dialog open={closing !== null} onClose={() => setClosing(null)} title={closing?.state === "purchase" ? t("board.move.closed") : t("board.move.cancel")}>
        <p className="mb-2 text-sm">{closing?.po_name}</p>
        <Label htmlFor="board-drag-note">{t("board.move.note")}</Label>
        <Textarea id="board-drag-note" value={note} onChange={(event) => setNote(event.target.value)} rows={3} />
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setClosing(null)}>
            {t("approvals.cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={move.isPending}
            onClick={async () => {
              if (closing) await perform(closing, "closed", note);
              setClosing(null);
            }}
          >
            {t("board.move.confirm")}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function BoardColumn({
  column,
  cards,
  total,
  canDrag,
  onOpen,
}: {
  column: Column;
  cards: BoardCard[];
  total: number;
  canDrag: boolean;
  onOpen: (card: BoardCard) => void;
}) {
  const { t } = useI18n();
  const { setNodeRef, isOver } = useDroppable({ id: column });
  // The two history columns start folded: a count, expanded on demand, so the
  // live columns keep the width.
  const foldable = column === "received" || column === "closed";
  const [expanded, setExpanded] = useState(!foldable);
  const count = cards.length === total ? String(total) : `${cards.length}/${total}`;
  if (!expanded) {
    return (
      <section
        ref={setNodeRef}
        aria-label={t(`board.col.${column}`)}
        className={cn("flex w-12 shrink-0 flex-col items-center rounded-lg border bg-muted/30 p-2", isOver ? "ring-2 ring-primary" : "")}
      >
        <button type="button" className="flex flex-col items-center gap-2 text-sm font-semibold" onClick={() => setExpanded(true)} title={t(`board.col.hint.${column}`)}>
          <Badge variant="secondary">{count}</Badge>
          <span className="[writing-mode:vertical-rl]">{t(`board.col.${column}`)}</span>
        </button>
      </section>
    );
  }
  return (
    <section
      ref={setNodeRef}
      aria-label={t(`board.col.${column}`)}
      className={cn("flex w-64 shrink-0 flex-col rounded-lg border bg-muted/30 p-2", isOver ? "ring-2 ring-primary" : "")}
    >
      <h2 className="mb-2 flex items-center justify-between px-1 text-sm font-semibold" title={t(`board.col.hint.${column}`)}>
        {foldable ? (
          <button type="button" className="text-left" onClick={() => setExpanded(false)} aria-label={t("board.fold", { column: t(`board.col.${column}`) })}>
            {t(`board.col.${column}`)}
          </button>
        ) : (
          t(`board.col.${column}`)
        )}
        <Badge variant="secondary">{count}</Badge>
      </h2>
      <div className="flex min-h-16 flex-1 flex-col gap-2 overflow-y-auto">
        {cards.length === 0 ? <p className="px-1 text-xs text-muted-foreground">{t("board.empty")}</p> : null}
        {cards.map((card) => (
          <OrderCard key={card.po_name} card={card} draggable={canDrag && targetsFor(card).length > 0} onOpen={() => onOpen(card)} />
        ))}
      </div>
    </section>
  );
}

function unique(values: string[]): string[] {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b));
}

function clean(search: BoardSearch): BoardSearch {
  return Object.fromEntries(Object.entries(search).filter(([, v]) => v !== undefined && v !== "")) as BoardSearch;
}
