/**
 * One PO-centred story: every case event in order (what came in, which rule
 * fired, what was asked of which agent, what it answered, who approved what),
 * then the agent runs that worked on it with model, tokens and cost. Emails
 * are entries with direction and links; bodies live in the approval preview
 * and in Langfuse only.
 */
import { Link, useParams } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate, formatDateTime, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { useCase, type AgentRun, type CaseEvent, type CaseView } from "./api";
import { CaseChat } from "@/features/chat/CaseChat";
import { agentName, failureOf, labelFor, shortCaseId } from "./labels";

export function CaseTimelinePage() {
  const { caseId } = useParams({ strict: false }) as { caseId: string };
  const { t, locale } = useI18n();
  const detail = useCase(caseId);
  if (detail.isPending) return <Loading />;
  if (detail.error) return <ErrorBox error={detail.error} onRetry={() => detail.refetch()} />;
  const { case: item, events, runs } = detail.data;
  return (
    <div>
      <PageTitle title={caseTitle(item, t, locale)}>
        <div className="flex items-center gap-2">
          <StatusBadge status={item.status} />
          {item.trace_url ? (
            <a href={item.trace_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-sm text-primary underline">
              {t("cases.trace")} <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      </PageTitle>
      <div className="grid gap-4 p-4 lg:grid-cols-[1fr_30rem] 2xl:grid-cols-[1fr_36rem]">
        <section>
          {item.summary ? <p className="mb-3 text-sm">{item.summary}</p> : null}
          <p className="mb-3 text-xs text-muted-foreground">
            {t("cases.opened", { at: formatDateTime(item.created_at, locale) })}
            {item.next_action_at ? ` · ${t("cases.next_action", { at: formatDateTime(item.next_action_at, locale) })}` : ""}
            {" · "}
            <span title={item.case_id}>{t("cases.id", { id: shortCaseId(item.case_id, item.code) })}</span>
          </p>
          <CaseEvents events={events} />
        </section>
        <aside className="flex flex-col gap-4">
          <CaseChat caseRef={item.code} compact />
          <div>
            <h2 className="mb-2 text-sm font-semibold">{t("cases.runs")}</h2>
            {runs.length === 0 ? <p className="text-sm text-muted-foreground">{t("cases.no_runs")}</p> : <RunsTable runs={runs} compact />}
          </div>
        </aside>
      </div>
    </div>
  );
}

export function caseTitle(item: CaseView, t: ReturnType<typeof useI18n>["t"], locale: string): string {
  const kind = t(`cases.kind.${item.kind}`);
  const what = item.po_name
    ? t("cases.title.po", { po: item.po_name, kind })
    : item.kind === "planning"
      ? t("cases.title.planning", { date: formatDate(item.created_at, locale) })
      : t("cases.title.kind", { kind, date: formatDate(item.created_at, locale) });
  return `${item.code} · ${what}`;
}

/** The case's story in order; shared by the case page and the board drawer. */
export function CaseEvents({ events }: { events: CaseEvent[] }) {
  const { t, locale } = useI18n();
  if (events.length === 0) return <Empty />;
  return (
    <ol className="relative border-l pl-4" aria-label={t("cases.timeline")}>
      {events.map((event) => (
        <li key={event.id} className="mb-4">
          <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-card bg-primary" />
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{formatDateTime(event.at, locale)}</span>
            <Badge variant="outline">{t(`cases.event.${event.kind}`)}</Badge>
          </div>
          <EventLine event={event} />
        </li>
      ))}
    </ol>
  );
}

function EventLine({ event }: { event: CaseEvent }) {
  const { t } = useI18n();
  const p = event.payload as Record<string, unknown>;
  const text = (key: string) => (typeof p[key] === "string" ? (p[key] as string) : "");
  switch (event.kind) {
    case "event_received":
      return (
        <p className="text-sm">
          {t("cases.line.event_received", {
            type: labelFor(t, "event", text("event_type")),
            source: labelFor(t, "source", text("source")),
          })}
          {text("sender_address") ? ` · ${t("cases.line.email", { sender: text("sender_address") })}` : ""}
          {text("web_link") ? (
            <>
              {" · "}
              <a href={text("web_link")} target="_blank" rel="noopener noreferrer" className="text-primary underline">
                {t("cases.line.open_email")}
              </a>
            </>
          ) : null}
        </p>
      );
    case "rule_fired":
      return (
        <p className="text-sm">
          <span className="font-medium">{text("rule")}</span>
          {text("reason") ? ` · ${text("reason")}` : ""}
          {p.escalate ? ` · ${t("cases.line.escalates")}` : text("task") ? ` · ${text("task")}` : ""}
        </p>
      );
    case "task_sent":
      return (
        <p className="text-sm">
          {t("cases.line.task_sent", {
            task: labelFor(t, "task", text("task")),
            agent: labelFor(t, "agent", text("agent")),
          })}
        </p>
      );
    case "result":
      return <ResultLine status={text("status") || "done"} summary={text("summary")} payload={p} />;
    case "approval_requested":
    case "approval_resolved": {
      const id = typeof p.approval_id === "number" ? p.approval_id : undefined;
      return (
        <p className="text-sm">
          {event.kind === "approval_requested"
            ? t("cases.line.approval_requested", { agent: labelFor(t, "agent", text("agent")) })
            : t("cases.line.approval_resolved", { status: text("status"), by: text("by") || text("resolved_by") || "" })}
          {id !== undefined ? (
            <>
              {" · "}
              <Link to="/approvals" search={{ id, tab: event.kind === "approval_resolved" ? "resolved" : undefined }} className="text-primary underline">
                {t("cases.line.open_approval", { id })}
              </Link>
            </>
          ) : null}
        </p>
      );
    }
    case "escalated":
      return <p className="text-sm">{text("summary") || text("reason")}</p>;
    case "note":
      return <p className="text-sm">{text("text")}</p>;
    case "chat":
      return (
        <p className="text-sm">
          <span className="text-muted-foreground">{text("role") === "user" ? (text("by") || t("chat.someone")) : t("chat.director")}: </span>
          {text("text")}
        </p>
      );
    case "promise":
      return (
        <p className="text-sm text-muted-foreground">
          {Object.entries(p)
            .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
            .join(" · ")}
        </p>
      );
    default:
      return <pre className="overflow-x-auto text-xs text-muted-foreground">{JSON.stringify(p)}</pre>;
  }
}

function ResultLine({ status, summary, payload }: { status: string; summary: string; payload: Record<string, unknown> }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const failure = status === "failed" ? failureOf(payload) : null;
  if (!failure) {
    return (
      <p className="text-sm">
        <StatusBadge status={status} /> {summary}
      </p>
    );
  }
  return (
    <div className="text-sm">
      <StatusBadge status={status} />{" "}
      <span className="text-destructive">{t("cases.line.failed", { message: failure.message })}</span>
      {failure.details ? (
        <>
          {" "}
          <button type="button" className="text-xs text-muted-foreground underline" onClick={() => setOpen((o) => !o)}>
            {t("cases.line.details")}
          </button>
          {open ? <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted p-2 text-xs">{failure.details}</pre> : null}
        </>
      ) : null}
    </div>
  );
}

export function RunsTable({ runs, showCase = false, compact = false }: { runs: AgentRun[]; showCase?: boolean; compact?: boolean }) {
  const { t, locale } = useI18n();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("runs.col.started")}</TableHead>
          <TableHead>{t("runs.col.agent")}</TableHead>
          {showCase ? <TableHead>{t("runs.col.po")}</TableHead> : null}
          <TableHead>{t("runs.col.status")}</TableHead>
          {compact ? null : <TableHead>{t("runs.col.model")}</TableHead>}
          {compact ? null : <TableHead className="text-right">{t("runs.col.tokens")}</TableHead>}
          {compact ? null : <TableHead className="text-right">{t("runs.col.cost")}</TableHead>}
          <TableHead className="text-right">{t("runs.col.duration")}</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(run.started_at, locale)}</TableCell>
            <TableCell>{agentName(t, run.agent)}</TableCell>
            {showCase ? <TableCell>{run.po_name ?? run.case_id ?? ""}</TableCell> : null}
            <TableCell>
              <StatusBadge status={run.status} />
            </TableCell>
            {compact ? null : <TableCell className="text-muted-foreground">{run.model ?? ""}</TableCell>}
            {compact ? null : (
              <TableCell className="text-right tabular-nums">
                {formatNumber(run.input_tokens, locale, 0)} / {formatNumber(run.output_tokens, locale, 0)}
              </TableCell>
            )}
            {compact ? null : <TableCell className="text-right tabular-nums">${formatNumber(run.cost_usd, locale, 4)}</TableCell>}
            <TableCell className="text-right tabular-nums">
              {run.duration_seconds !== null && run.duration_seconds !== undefined ? `${formatNumber(run.duration_seconds, locale, 1)} s` : ""}
            </TableCell>
            <TableCell>
              {run.trace_url ? (
                <a href={run.trace_url} target="_blank" rel="noopener noreferrer" className="text-primary underline">
                  {t("runs.trace")}
                </a>
              ) : null}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
