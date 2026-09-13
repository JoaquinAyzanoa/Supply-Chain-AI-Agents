/**
 * The approvals inbox: a filterable list on the left, the selected approval on
 * the right (full screen on a phone). Phase 11 S8 adds bulk decisions with
 * filters (kind, email kind, supplier), keyboard shortcuts (j/k to move, a to
 * approve, r to reject, e to edit) and the diff of what an approver changed.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useAuth } from "@/auth/AuthProvider";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { useApproval, useApprovals, useBulkResolve } from "./api";
import { ApprovalDetail } from "./ApprovalDetail";
import { daysSince, type Approval } from "./types";

export interface ApprovalsSearch {
  id?: number;
  tab?: "pending" | "resolved";
  kind?: string;
  po?: string;
  email?: string;
  supplier?: string;
}

const KINDS = [
  "send_email",
  "po_change",
  "planning_run",
  "escalation",
  "vendor_bill",
  "supplier_score",
  "award",
  "negotiation_offer",
  "partner_create",
  "internal_request",
  "price_list_update",
] as const;
const EMAIL_KINDS = ["rfq", "send_po", "follow_up", "request_eta", "reply", "answer", "decline", "counter_offer"] as const;

/** Shortcuts are dispatched to the open detail as a DOM event; it decides what they do. */
export const SHORTCUT_EVENT = "approvals:shortcut";

function facts(row: Approval): Record<string, unknown> {
  const payload = row.payload as Record<string, unknown>;
  return (payload.facts as Record<string, unknown> | undefined) ?? {};
}

export function ApprovalsInbox() {
  const { t } = useI18n();
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as ApprovalsSearch;
  const tab = search.tab ?? "pending";
  const status = tab === "pending" ? "pending" : "all";
  const list = useApprovals({ status, kind: search.kind, po: search.po });
  const rows = useMemo(
    () =>
      (list.data ?? []).filter((row) => {
        if (tab === "pending" ? row.status !== "pending" : row.status === "pending") return false;
        if (search.email && String(facts(row).email_kind ?? "") !== search.email) return false;
        if (search.supplier) {
          const needle = search.supplier.toLowerCase();
          const name = String(facts(row).partner_name ?? "");
          if (!name.toLowerCase().includes(needle) && !row.summary.toLowerCase().includes(needle)) return false;
        }
        return true;
      }),
    [list.data, tab, search.email, search.supplier],
  );
  const selected = useApproval(search.id);
  const bulk = useBulkResolve();
  const [ticked, setTicked] = useState<Set<number>>(new Set());
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const canAct = hasRole("approver") && tab === "pending";

  const setSearch = (patch: Partial<ApprovalsSearch>) =>
    void navigate({ to: "/approvals", search: (prev: ApprovalsSearch) => clean({ ...prev, ...patch }) });

  // When nothing is selected on a wide screen, show the first row.
  useEffect(() => {
    const wide = typeof window.matchMedia === "function" && window.matchMedia("(min-width: 640px)").matches;
    if (search.id === undefined && rows.length && wide) {
      setSearch({ id: rows[0]!.id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length, search.id]);

  // Keyboard: j/k move through the list; a, r and e reach the open detail.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) return;
      if (event.key === "j" || event.key === "k") {
        if (!rows.length) return;
        const index = rows.findIndex((row) => row.id === search.id);
        const next = event.key === "j" ? Math.min(rows.length - 1, index + 1) : Math.max(0, index - 1);
        event.preventDefault();
        setSearch({ id: rows[next]!.id });
      } else if (event.key === "a" || event.key === "r" || event.key === "e") {
        if (search.id === undefined) return;
        window.dispatchEvent(new CustomEvent(SHORTCUT_EVENT, { detail: { key: event.key, id: search.id } }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, search.id]);

  const toggle = (id: number, on: boolean) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const allTicked = rows.length > 0 && rows.every((row) => ticked.has(row.id));
  const decide = async (status: "approved" | "rejected") => {
    const ids = rows.filter((row) => ticked.has(row.id)).map((row) => row.id);
    if (!ids.length) return;
    setNotice(null);
    try {
      const result = await bulk.mutateAsync({ ids, status, reason: status === "rejected" ? reason || null : null });
      const failed = result.results.filter((r) => r.error);
      setNotice(t("approvals.bulk.done", { n: result.resolved, failed: failed.length }));
      setTicked(new Set());
      setRejecting(false);
      setReason("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    }
  };

  const showDetail = search.id !== undefined;
  return (
    <div className="flex h-[calc(100vh-0px)] flex-col sm:h-screen">
      <PageTitle title={t("approvals.title")}>
        <div className="flex flex-wrap items-center gap-2">
          <TabButton active={tab === "pending"} onClick={() => setSearch({ tab: undefined, id: undefined })}>
            {t("approvals.tab.pending")}
            {list.data && tab === "pending" ? <Badge variant="secondary">{rows.length}</Badge> : null}
          </TabButton>
          <TabButton active={tab === "resolved"} onClick={() => setSearch({ tab: "resolved", id: undefined })}>
            {t("approvals.tab.resolved")}
          </TabButton>
          <select
            aria-label={t("approvals.filter.kind")}
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.kind ?? ""}
            onChange={(event) => setSearch({ kind: event.target.value || undefined, id: undefined })}
          >
            <option value="">{t("approvals.filter.all_kinds")}</option>
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`approvals.kind.${kind}`)}
              </option>
            ))}
          </select>
          {(search.kind ?? "send_email") === "send_email" ? (
            <select
              aria-label={t("approvals.filter.email")}
              className="h-8 rounded-md border bg-card px-2 text-sm"
              value={search.email ?? ""}
              onChange={(event) => setSearch({ email: event.target.value || undefined, id: undefined })}
            >
              <option value="">{t("approvals.filter.all_emails")}</option>
              {EMAIL_KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {t(`approvals.email_kind.${kind}`)}
                </option>
              ))}
            </select>
          ) : null}
          <Input
            aria-label={t("approvals.filter.supplier")}
            placeholder={t("approvals.filter.supplier")}
            className="h-8 w-36"
            defaultValue={search.supplier ?? ""}
            onKeyDown={(event) => {
              if (event.key === "Enter") setSearch({ supplier: (event.target as HTMLInputElement).value || undefined, id: undefined });
            }}
            onBlur={(event) => setSearch({ supplier: event.target.value || undefined })}
          />
          <Input
            aria-label={t("approvals.filter.po")}
            placeholder={t("approvals.filter.po")}
            className="h-8 w-32"
            defaultValue={search.po ?? ""}
            onKeyDown={(event) => {
              if (event.key === "Enter") setSearch({ po: (event.target as HTMLInputElement).value || undefined, id: undefined });
            }}
            onBlur={(event) => setSearch({ po: event.target.value || undefined })}
          />
        </div>
      </PageTitle>
      {canAct && (rows.length || notice) ? (
        <div className="flex flex-wrap items-center gap-2 border-b bg-card px-4 py-1.5 text-sm" aria-label={t("approvals.bulk.title")}>
          <label className="flex items-center gap-1">
            <input type="checkbox" aria-label={t("approvals.bulk.select_all")} checked={allTicked} onChange={(event) => setTicked(event.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} />
            {t("approvals.bulk.select_all")}
          </label>
          <span className="text-muted-foreground">{t("approvals.bulk.selected", { n: ticked.size })}</span>
          <Button size="sm" disabled={!ticked.size || bulk.isPending} onClick={() => void decide("approved")}>
            {t("approvals.bulk.approve", { n: ticked.size })}
          </Button>
          <Button size="sm" variant="destructive" disabled={!ticked.size || bulk.isPending} onClick={() => setRejecting(true)}>
            {t("approvals.bulk.reject", { n: ticked.size })}
          </Button>
          <span className="ml-auto hidden text-xs text-muted-foreground sm:inline">{t("approvals.shortcuts")}</span>
          {notice ? (
            <span className="w-full text-xs text-muted-foreground" role="status">
              {notice}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <aside className={cn("w-full overflow-y-auto border-r sm:block sm:w-80 lg:w-96", showDetail ? "hidden" : "block")}>
          {list.isPending ? <Loading /> : null}
          {list.error ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
          {list.data && rows.length === 0 ? <Empty text={t("approvals.empty")} /> : null}
          <ul>
            {rows.map((row) => (
              <li key={row.id} className="flex items-stretch">
                {canAct ? (
                  <label className="flex items-center border-b px-2">
                    <input type="checkbox" aria-label={t("approvals.bulk.tick", { id: row.id })} checked={ticked.has(row.id)} onChange={(event) => toggle(row.id, event.target.checked)} />
                  </label>
                ) : null}
                <ApprovalRow row={row} active={row.id === search.id} onSelect={() => setSearch({ id: row.id })} />
              </li>
            ))}
          </ul>
        </aside>
        <section className={cn("min-w-0 flex-1", showDetail ? "block" : "hidden sm:block")}>
          {!showDetail ? <Empty text={t("approvals.select")} /> : null}
          {showDetail && selected.isPending ? <Loading /> : null}
          {showDetail && selected.error ? <ErrorBox error={selected.error} onRetry={() => selected.refetch()} /> : null}
          {selected.data ? <ApprovalDetail approval={selected.data} onBack={() => setSearch({ id: undefined })} /> : null}
        </section>
      </div>
      <Dialog open={rejecting} onClose={() => setRejecting(false)} title={t("approvals.bulk.reject_title", { n: ticked.size })}>
        <div className="flex flex-col gap-3">
          <Label htmlFor="bulk-reason">{t("approvals.reject_reason")}</Label>
          <Textarea id="bulk-reason" value={reason} onChange={(event) => setReason(event.target.value)} rows={3} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setRejecting(false)}>
              {t("approvals.cancel")}
            </Button>
            <Button variant="destructive" onClick={() => void decide("rejected")} disabled={bulk.isPending}>
              {t("approvals.reject_confirm")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}

function clean(search: ApprovalsSearch): ApprovalsSearch {
  return Object.fromEntries(Object.entries(search).filter(([, v]) => v !== undefined && v !== "")) as ApprovalsSearch;
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Button variant={active ? "secondary" : "ghost"} size="sm" onClick={onClick} aria-pressed={active}>
      {children}
    </Button>
  );
}

function ApprovalRow({ row, active, onSelect }: { row: Approval; active: boolean; onSelect: () => void }) {
  const { t } = useI18n();
  const age = daysSince(row.created_at);
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex w-full min-w-0 flex-col gap-1 border-b px-3 py-2 text-left hover:bg-accent/50",
        active ? "bg-accent" : "",
      )}
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Badge variant="outline">{t(`approvals.kind.${row.kind}`)}</Badge>
        {row.po_name ? <span className="font-medium text-foreground">{row.po_name}</span> : null}
        <span className="ml-auto">{age > 0 ? t("approvals.age", { n: age }) : t("approvals.today")}</span>
      </div>
      <div className="line-clamp-2 text-sm">{row.summary}</div>
    </button>
  );
}
