/**
 * Playbooks: the multi-step plans the director runs on an order (a late
 * order, a silent RFQ, a receipt difference). The page shows each plan's
 * steps with how many orders sit on each one, the active runs with where
 * they are and when they move next, and lets an approver start a plan on an
 * order by hand or cancel a run. Steps are code and YAML; the model never
 * decides the sequence.
 */
import { Link } from "@tanstack/react-router";
import { Bot, Clock, ListChecks, Play, RefreshCw, UserCheck, XCircle } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { agentName, labelFor } from "@/features/cases/labels";
import { useCancelRun, usePlaybookRuns, usePlaybooks, useStartPlaybook, useTickPlaybooks, type PlaybookView, type RunView, type StepView } from "./api";

export function PlaybooksPage() {
  const { t } = useI18n();
  const { hasRole } = useAuth();
  const playbooks = usePlaybooks();
  const [showFinished, setShowFinished] = useState(false);
  const runs = usePlaybookRuns(!showFinished);
  const tick = useTickPlaybooks();
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");

  if (playbooks.isPending) return <Loading />;
  if (playbooks.error) return <ErrorBox error={playbooks.error} onRetry={() => playbooks.refetch()} />;

  const moveNow = async () => {
    const result = (await tick.mutateAsync()) as { moved?: number[]; active?: number };
    setMessage(t("playbooks.ticked", { moved: result.moved?.length ?? 0, active: result.active ?? 0 }));
  };

  return (
    <div>
      <PageTitle title={t("playbooks.title")}>
        {canAct ? (
          <Button variant="outline" size="sm" onClick={() => void moveNow()} disabled={tick.isPending}>
            <RefreshCw className="h-4 w-4" /> {t("playbooks.move_now")}
          </Button>
        ) : null}
      </PageTitle>
      <div className="space-y-6 p-4">
        <p className="max-w-3xl text-sm text-muted-foreground">{t("playbooks.intro")}</p>
        {message ? (
          <p className="text-sm text-muted-foreground" role="status">
            {message}
          </p>
        ) : null}

        <section className="grid gap-3 lg:grid-cols-2">
          {playbooks.data.map((playbook) => (
            <PlaybookCard key={playbook.name} playbook={playbook} />
          ))}
        </section>

        {canAct ? <StartForm playbooks={playbooks.data} onDone={setMessage} /> : null}

        <section className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold">{showFinished ? t("playbooks.runs.recent") : t("playbooks.runs.active")}</h2>
            <Button variant="ghost" size="sm" onClick={() => setShowFinished((value) => !value)}>
              {showFinished ? t("playbooks.runs.show_active") : t("playbooks.runs.show_recent")}
            </Button>
          </div>
          {runs.isPending ? <Loading /> : runs.error ? <ErrorBox error={runs.error} onRetry={() => runs.refetch()} /> : <RunsTable rows={runs.data} canAct={canAct} onDone={setMessage} />}
        </section>
      </div>
    </div>
  );
}

function PlaybookCard({ playbook }: { playbook: PlaybookView }) {
  const { t } = useI18n();
  return (
    <article className="rounded-md border bg-card p-3" aria-label={playbook.title}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <ListChecks className="h-4 w-4" /> {playbook.title}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{playbook.description}</p>
        </div>
        <Badge variant={playbook.active_runs ? "warning" : "outline"}>{t("playbooks.active_n", { n: playbook.active_runs })}</Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("playbooks.trigger", { trigger: t(`playbooks.trigger.${playbook.trigger}`) })}</p>
      <ol className="mt-2 space-y-1">
        {playbook.steps.map((step, index) => (
          <StepLine key={step.id} step={step} index={index} />
        ))}
      </ol>
    </article>
  );
}

function StepLine({ step, index }: { step: StepView; index: number }) {
  const { t } = useI18n();
  const Icon = step.kind === "agent" ? Bot : step.kind === "wait" ? Clock : UserCheck;
  const detail =
    step.kind === "agent"
      ? t("playbooks.step.agent", { agent: agentName(t, step.agent), task: labelFor(t, "task", step.task ?? "") })
      : step.kind === "wait"
        ? t("playbooks.step.wait", { days: step.wait_days ?? 0 }) + (step.until ? ` · ${t("playbooks.step.until", { condition: t(`playbooks.cond.${step.until}`) })}` : "")
        : t(`playbooks.step.action.${step.action ?? "escalate"}`);
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className="w-5 shrink-0 text-right tabular-nums text-muted-foreground">{index + 1}.</span>
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span>{step.label}</span>
        <span className="block text-xs text-muted-foreground">
          {detail}
          {step.when !== "always" ? ` · ${t("playbooks.step.when", { condition: t(`playbooks.cond.${step.when}`) })}` : ""}
        </span>
      </span>
      {step.active_runs ? <Badge variant="secondary">{step.active_runs}</Badge> : null}
    </li>
  );
}

function StartForm({ playbooks, onDone }: { playbooks: PlaybookView[]; onDone: (message: string) => void }) {
  const { t } = useI18n();
  const start = useStartPlaybook();
  const [playbook, setPlaybook] = useState(playbooks[0]?.name ?? "");
  const [poName, setPoName] = useState("");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = poName.trim().toUpperCase();
    if (!name) return;
    try {
      const result = await start.mutateAsync({ playbook, po_name: name, partner_id: null });
      onDone(t("playbooks.started", { po: name, title: result.position.title, step: result.position.step_label ?? "" }));
      setPoName("");
    } catch (error) {
      onDone(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-wrap items-end gap-2 rounded-md border bg-card p-3" aria-label={t("playbooks.start.title")}>
      <div>
        <Label htmlFor="pb-name">{t("playbooks.start.playbook")}</Label>
        <select id="pb-name" className="h-9 rounded-md border bg-background px-2 text-sm" value={playbook} onChange={(event) => setPlaybook(event.target.value)}>
          {playbooks.map((item) => (
            <option key={item.name} value={item.name}>
              {item.title}
            </option>
          ))}
        </select>
      </div>
      <div>
        <Label htmlFor="pb-po">{t("playbooks.start.po")}</Label>
        <Input id="pb-po" value={poName} onChange={(event) => setPoName(event.target.value)} placeholder="P00077" className="w-32" />
      </div>
      <Button type="submit" size="sm" disabled={start.isPending || !poName.trim()}>
        <Play className="h-4 w-4" /> {t("playbooks.start.button")}
      </Button>
      <p className="basis-full text-xs text-muted-foreground">{t("playbooks.start.hint")}</p>
    </form>
  );
}

function RunsTable({ rows, canAct, onDone }: { rows: RunView[]; canAct: boolean; onDone: (message: string) => void }) {
  const { t, locale } = useI18n();
  const cancel = useCancelRun();
  if (!rows.length) return <Empty text={t("playbooks.runs.empty")} />;
  const stop = async (row: RunView) => {
    try {
      await cancel.mutateAsync(row.run.id);
      onDone(t("playbooks.cancelled", { po: row.run.po_name ?? String(row.run.id) }));
    } catch (error) {
      onDone(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("playbooks.runs.order")}</TableHead>
            <TableHead>{t("playbooks.runs.playbook")}</TableHead>
            <TableHead>{t("playbooks.runs.step")}</TableHead>
            <TableHead>{t("playbooks.runs.status")}</TableHead>
            <TableHead>{t("playbooks.runs.due")}</TableHead>
            <TableHead>{t("playbooks.runs.started")}</TableHead>
            {canAct ? <TableHead /> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.run.id}>
              <TableCell className="font-medium">
                {row.run.po_name ? (
                  <Link to="/board" search={{ po: row.run.po_name }} className="text-primary underline">
                    {row.run.po_name}
                  </Link>
                ) : (
                  "—"
                )}
              </TableCell>
              <TableCell>{row.position.title}</TableCell>
              <TableCell>
                {row.position.step_label ? `${t("playbooks.step_of", { n: row.position.step_index + 1, total: row.position.steps_total })} · ${row.position.step_label}` : row.run.summary ?? "—"}
              </TableCell>
              <TableCell>
                <Badge variant={row.run.status === "done" ? "success" : row.run.status === "failed" ? "destructive" : row.run.status === "cancelled" ? "outline" : "warning"}>
                  {t(`playbooks.status.${row.run.status}`)}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{row.run.due_at ? formatDateTime(row.run.due_at, locale) : "—"}</TableCell>
              <TableCell className="text-muted-foreground">
                {formatDateTime(row.run.started_at, locale)}
                {row.run.started_by ? ` · ${row.run.started_by}` : ""}
              </TableCell>
              {canAct ? (
                <TableCell className="text-right">
                  {["running", "waiting", "waiting_approval"].includes(row.run.status) ? (
                    <Button variant="ghost" size="sm" onClick={() => void stop(row)} disabled={cancel.isPending} aria-label={t("playbooks.cancel_run", { po: row.run.po_name ?? String(row.run.id) })}>
                      <XCircle className="h-4 w-4" /> {t("playbooks.cancel")}
                    </Button>
                  ) : null}
                </TableCell>
              ) : null}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
