/**
 * The approvals inbox: a filterable list on the left, the selected approval on
 * the right. On a phone the two stack: the list, then the detail with a back
 * button. The selection lives in the URL (?id=) so links from other screens and
 * notifications open a specific approval.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { useApproval, useApprovals } from "./api";
import { ApprovalDetail } from "./ApprovalDetail";
import { daysSince, type Approval } from "./types";

export interface ApprovalsSearch {
  id?: number;
  tab?: "pending" | "resolved";
  kind?: string;
  po?: string;
}

const KINDS = ["send_email", "po_change", "planning_run", "escalation"] as const;

export function ApprovalsInbox() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as ApprovalsSearch;
  const tab = search.tab ?? "pending";
  const status = tab === "pending" ? "pending" : "all";
  const list = useApprovals({ status, kind: search.kind, po: search.po });
  const rows = (list.data ?? []).filter((row) => (tab === "pending" ? row.status === "pending" : row.status !== "pending"));
  const selected = useApproval(search.id);

  const setSearch = (patch: Partial<ApprovalsSearch>) =>
    void navigate({ to: "/", search: (prev: ApprovalsSearch) => clean({ ...prev, ...patch }) });

  // When nothing is selected on a wide screen, show the first row.
  useEffect(() => {
    const wide = typeof window.matchMedia === "function" && window.matchMedia("(min-width: 640px)").matches;
    if (search.id === undefined && rows.length && wide) {
      setSearch({ id: rows[0]!.id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length, search.id]);

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

      <div className="flex min-h-0 flex-1">
        <aside className={cn("w-full overflow-y-auto border-r sm:block sm:w-80 lg:w-96", showDetail ? "hidden" : "block")}>
          {list.isPending ? <Loading /> : null}
          {list.error ? <ErrorBox error={list.error} onRetry={() => list.refetch()} /> : null}
          {list.data && rows.length === 0 ? <Empty text={t("approvals.empty")} /> : null}
          <ul>
            {rows.map((row) => (
              <li key={row.id}>
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
        "flex w-full flex-col gap-1 border-b px-3 py-2 text-left hover:bg-accent/50",
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
