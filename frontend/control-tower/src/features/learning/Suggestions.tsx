/**
 * What the weekly calibration proposes from people's decisions: a rule where the
 * same action was always approved unchanged, a wider tolerance, or attention where
 * proposals are mostly rejected. Accepting a rule still goes through a second person.
 */
import { Link } from "@tanstack/react-router";
import { Check, Lightbulb, X } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { useFeedbackStats, useSuggestionAction, useSuggestions, type Suggestion } from "./api";

export function Suggestions() {
  const { t } = useI18n();
  const { hasRole } = useAuth();
  const suggestions = useSuggestions();
  const act = useSuggestionAction();
  const [message, setMessage] = useState<string | null>(null);

  const run = async (suggestion: Suggestion, action: "accept" | "dismiss") => {
    setMessage(null);
    try {
      const result = await act.mutateAsync({ id: suggestion.id, action });
      if (action === "dismiss") setMessage(t("learning.dismissed"));
      else if ("approval_id" in result && result.approval_id) setMessage(t("learning.accepted_approval", { id: result.approval_id }));
      else if ("saved_version" in result && result.saved_version) setMessage(t("learning.accepted_saved", { version: result.saved_version }));
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };

  if (suggestions.isPending) return <Loading />;
  if (suggestions.error) return <ErrorBox error={suggestions.error} onRetry={() => suggestions.refetch()} />;
  return (
    <div className="flex flex-col gap-2" data-testid="suggestions">
      {suggestions.data.length === 0 ? <p className="text-sm text-muted-foreground">{t("learning.no_suggestions")}</p> : null}
      {suggestions.data.map((s) => (
        <div key={s.id} className="flex flex-wrap items-start gap-2 rounded-md border bg-card p-3 text-sm">
          <Lightbulb className="mt-0.5 h-4 w-4 shrink-0 text-warning-text" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1">
              <span className="font-medium">{s.title}</span>
              <Badge variant="outline">{t(`learning.kind.${s.kind}`)}</Badge>
            </div>
            <p className="text-muted-foreground">{s.detail}</p>
          </div>
          {s.kind !== "attention" && hasRole("admin") ? (
            <Button size="sm" onClick={() => run(s, "accept")} disabled={act.isPending}>
              <Check className="h-4 w-4" /> {t("learning.accept")}
            </Button>
          ) : null}
          {hasRole("approver") ? (
            <Button size="sm" variant="outline" onClick={() => run(s, "dismiss")} disabled={act.isPending}>
              <X className="h-4 w-4" /> {t("learning.dismiss")}
            </Button>
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

export function DecisionStats({ days = 90 }: { days?: number }) {
  const { t } = useI18n();
  const stats = useFeedbackStats(days);
  if (stats.isPending) return <Loading />;
  if (stats.error) return <ErrorBox error={stats.error} onRetry={() => stats.refetch()} />;
  const s = stats.data;
  const pct = (n: number) => (s.total ? `${Math.round((n / s.total) * 100)}%` : "—");
  return (
    <div className="flex flex-col gap-2" data-testid="decision-stats">
      <p className="text-sm">{t("learning.stats_summary", { total: s.total, days: s.days, unchanged: pct(s.unchanged), edited: pct(s.edited), rejected: pct(s.rejected) })}</p>
      {s.by_kind.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("learning.col.kind")}</TableHead>
              <TableHead className="text-right">{t("learning.col.n")}</TableHead>
              <TableHead className="text-right">{t("learning.col.unchanged")}</TableHead>
              <TableHead className="text-right">{t("learning.col.edited")}</TableHead>
              <TableHead className="text-right">{t("learning.col.rejected")}</TableHead>
              <TableHead className="text-right">{t("learning.col.minutes")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {s.by_kind.map((row) => (
              <TableRow key={row.kind}>
                <TableCell>{t(`approvals.kind.${row.kind}`)}</TableCell>
                <TableCell className="text-right tabular-nums">{row.n}</TableCell>
                <TableCell className="text-right tabular-nums">{row.unchanged}</TableCell>
                <TableCell className="text-right tabular-nums">{row.edited}</TableCell>
                <TableCell className="text-right tabular-nums">{row.rejected}</TableCell>
                <TableCell className="text-right tabular-nums">{row.median_minutes ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      <Link to="/approvals" search={{ tab: "resolved" }} className="text-sm text-primary underline">
        {t("learning.open_resolved")}
      </Link>
    </div>
  );
}
