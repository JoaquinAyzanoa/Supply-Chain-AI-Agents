/**
 * Five columns of what needs an eye. Order cards carry the follow-up policy's
 * next step and its date; "Act now" runs that step immediately (the director
 * answers 202 and the case timeline shows the outcome).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink, Zap } from "lucide-react";
import { useState } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { useI18n } from "@/i18n";
import { formatDate } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

type Board = Schemas["ExceptionsBoard"];
type Item = Schemas["ExceptionItem"];
type Column = keyof Omit<Board, "as_of">;

const COLUMNS: Column[] = ["late_pos", "rfqs_no_reply", "unlinked_mails", "failed_runs", "stale_approvals"];
const ACTIONABLE = new Set(["late_po", "rfq_no_reply"]);

export function useExceptions() {
  return useQuery({
    queryKey: ["exceptions"],
    queryFn: async () => unwrap(await api.GET("/api/exceptions")),
  });
}

export function ExceptionsBoardPage() {
  const { t, locale } = useI18n();
  const board = useExceptions();
  return (
    <div>
      <PageTitle title={t("exceptions.title")}>
        {board.data ? <span className="text-xs text-muted-foreground">{t("exceptions.as_of", { date: formatDate(board.data.as_of, locale) })}</span> : null}
      </PageTitle>
      {board.isPending ? <Loading /> : null}
      {board.error ? <ErrorBox error={board.error} onRetry={() => board.refetch()} /> : null}
      {board.data ? (
        <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-5">
          {COLUMNS.map((column) => (
            <section key={column} aria-label={t(`exceptions.col.${column}`)} className="rounded-lg border bg-muted/30 p-2">
              <h2 className="mb-2 flex items-center justify-between px-1 text-sm font-semibold">
                {t(`exceptions.col.${column}`)}
                <Badge variant="secondary">{board.data[column].length}</Badge>
              </h2>
              <div className="flex flex-col gap-2">
                {board.data[column].length === 0 ? <p className="px-1 text-xs text-muted-foreground">{t("exceptions.none")}</p> : null}
                {board.data[column].map((item) => (
                  <ExceptionCard key={`${item.kind}:${item.po_name ?? item.case_id ?? item.approval_id}`} item={item} />
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ExceptionCard({ item }: { item: Item }) {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/exceptions/{kind}/{po_name}/act", {
          params: { path: { kind: item.kind, po_name: item.po_name ?? "" } },
        }),
      ),
    onSuccess: () => {
      setMessage(t("exceptions.acted"));
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : String(error)),
  });
  const canAct = ACTIONABLE.has(item.kind) && item.can_act && hasRole("approver") && Boolean(item.po_name);
  return (
    <article className="rounded-md border bg-card p-2 text-sm shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium" title={item.title}>
            {item.title}
          </div>
          <div className="text-xs text-muted-foreground">{item.detail}</div>
        </div>
        {item.days > 0 ? <Badge variant={item.days >= 7 ? "destructive" : "warning"}>{t("exceptions.days", { n: item.days })}</Badge> : null}
      </div>
      {item.next_action ? (
        <div className="mt-1 text-xs">
          <span className="text-muted-foreground">{t("exceptions.next")}: </span>
          {item.next_action}
          {item.next_action_at ? ` · ${formatDate(item.next_action_at, locale)}` : ""}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {canAct ? (
          <Button size="sm" variant="outline" onClick={() => act.mutate()} disabled={act.isPending || message !== null}>
            <Zap className="h-3 w-3" /> {t("exceptions.act")}
          </Button>
        ) : null}
        {item.case_id ? (
          <Link to="/cases/$caseId" params={{ caseId: item.case_id }} className="text-xs text-primary underline">
            {t("exceptions.case")}
          </Link>
        ) : null}
        {item.approval_id ? (
          <Link to="/" search={{ id: item.approval_id }} className="text-xs text-primary underline">
            {t("exceptions.approval")}
          </Link>
        ) : null}
        {item.odoo_url ? (
          <a href={item.odoo_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-primary underline">
            Odoo <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      {message ? (
        <p className="mt-1 text-xs text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </article>
  );
}
