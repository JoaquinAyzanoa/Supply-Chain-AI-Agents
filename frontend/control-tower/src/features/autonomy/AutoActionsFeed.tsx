/**
 * "Done automatically": what the agents did without asking, by rule of the
 * autonomy policy, newest first. An action whose window is still open can be
 * reverted by an approver; an email cannot be unsent and says so.
 */
import { Link } from "@tanstack/react-router";
import { Undo2 } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { agentName } from "@/features/cases/labels";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { useAutoActions, useRevertAction, type AutoAction } from "./api";

export function AutoActionsFeed({ days = 7, compact = false }: { days?: number; compact?: boolean }) {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const actions = useAutoActions(days);
  const revert = useRevertAction();
  const [message, setMessage] = useState<string | null>(null);

  const undo = async (action: AutoAction) => {
    setMessage(null);
    try {
      const done = await revert.mutateAsync(action.id);
      setMessage(done.note);
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };

  if (actions.isPending) return <Loading />;
  if (actions.error) return <ErrorBox error={actions.error} onRetry={() => actions.refetch()} />;
  if (actions.data.length === 0) return <Empty text={t("autonomy.feed.empty", { days })} />;
  const rows = compact ? actions.data.slice(0, 8) : actions.data;
  return (
    <div className="flex flex-col gap-2" data-testid="auto-actions">
      {rows.map((action) => (
        <div key={action.id} className="flex flex-wrap items-start gap-2 rounded-md border bg-card p-2 text-sm">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1">
              <Badge variant={action.level === "auto_notice" ? "warning" : "secondary"}>{t(`autonomy.level.${action.level}`)}</Badge>
              <Badge variant="outline">{t(`approvals.kind.${action.kind}`)}</Badge>
              {action.po_name ? (
                <Link to="/" search={{ po: action.po_name }} className="text-primary underline">
                  {action.po_name}
                </Link>
              ) : null}
              {action.reverted_at ? <Badge variant="destructive">{t("autonomy.feed.reverted", { by: action.reverted_by ?? "-" })}</Badge> : null}
            </div>
            <p className="mt-1">{action.summary}</p>
            <p className="text-xs text-muted-foreground">
              {t("autonomy.feed.line", { agent: agentName(t, action.agent), rule: action.rule_id ?? "-", at: formatDateTime(action.created_at, locale) })}
              {action.revert_until && !action.reverted_at ? ` · ${t("autonomy.feed.until", { at: formatDateTime(action.revert_until, locale) })}` : ""}
            </p>
          </div>
          {action.revertible && hasRole("approver") ? (
            <Button variant="outline" size="sm" onClick={() => undo(action)} disabled={revert.isPending}>
              <Undo2 className="h-4 w-4" /> {t("autonomy.feed.revert")}
            </Button>
          ) : action.kind === "send_email" ? (
            <span className="text-xs text-muted-foreground">{t("autonomy.feed.no_unsend")}</span>
          ) : null}
        </div>
      ))}
      {message ? (
        <p className="text-sm text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}
