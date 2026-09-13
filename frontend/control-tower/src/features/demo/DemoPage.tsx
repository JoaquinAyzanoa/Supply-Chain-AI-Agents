/**
 * Demo mode: the scripted ten-minute scenario, one step at a time. The presenter sees where
 * the script stands, what to say and where the audience looks, runs the next step (deciding
 * the approvals it raises in the inbox, or letting the page decide them), and resets the
 * demo orders to their start state.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clock, Play, RotateCcw, XCircle } from "lucide-react";
import { useState } from "react";

import { api, ApiError, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { RecordLink } from "@/components/RecordLink";
import { useI18n } from "@/i18n";
import { cn, formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export type DemoView = Schemas["DemoView"];
export type DemoStep = Schemas["DemoStep"];
export type DemoStepOutcome = Schemas["DemoStepOutcome"];

export function useDemo() {
  return useQuery({
    queryKey: ["demo"],
    queryFn: async () => unwrap(await api.GET("/api/demo")) as DemoView,
  });
}

export function DemoPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const demo = useDemo();
  const [approve, setApprove] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["demo"] });
    void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    void queryClient.invalidateQueries({ queryKey: ["board"] });
    void queryClient.invalidateQueries({ queryKey: ["home"] });
    void queryClient.invalidateQueries({ queryKey: ["briefing"] });
  };
  const onError = (error: unknown) => setMessage(error instanceof ApiError ? error.message : String(error));
  const next = useMutation({
    mutationFn: async (step?: string) => unwrap(await api.POST("/api/demo/next", { body: { approve, step: step ?? null } })) as DemoView,
    onSuccess: (view) => {
      const last = view.outcomes[view.outcomes.length - 1];
      setMessage(last ? `${t(`demo.status.${last.status}`)}: ${last.summary}` : null);
      refresh();
    },
    onError,
  });
  const reset = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/demo/reset")) as DemoView,
    onSuccess: (view) => {
      const notes = (view.records.reset_notes as string[] | undefined) ?? [];
      setMessage(notes.length ? `${t("demo.reset_done")} ${notes.join("; ")}` : t("demo.reset_done"));
      refresh();
    },
    onError,
  });

  if (demo.isPending) return <Loading />;
  if (demo.error) return <ErrorBox error={demo.error} onRetry={() => demo.refetch()} />;
  const view = demo.data;
  const busy = next.isPending || reset.isPending;
  const outcomeOf = (key: string) => view.outcomes.find((o) => o.key === key);
  return (
    <div>
      <PageTitle title={t("demo.title")}>
        {canAct ? (
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-sm">
              <input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} />
              {t("demo.approve_for_me")}
            </label>
            <Button size="sm" variant="outline" onClick={() => reset.mutate()} disabled={busy}>
              <RotateCcw className="h-4 w-4" /> {t("demo.reset")}
            </Button>
            <Button size="sm" onClick={() => next.mutate(undefined)} disabled={busy || !view.started_at || view.finished}>
              <Play className="h-4 w-4" /> {view.next ? t("demo.next_step", { n: view.position + 1 }) : t("demo.finished")}
            </Button>
          </div>
        ) : null}
      </PageTitle>
      <div className="space-y-4 p-4">
        {(view.ready.notes ?? []).length ? (
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm" role="note" aria-label={t("demo.not_ready")}>
            <p className="font-medium">{t("demo.not_ready")}</p>
            <ul className="mt-1 list-disc pl-5">
              {(view.ready.notes ?? []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground" aria-label={t("demo.position")}>
          <span>{view.started_at ? t("demo.started", { at: formatDateTime(view.started_at, locale) }) : t("demo.not_started")}</span>
          <Badge variant={view.finished ? "success" : "secondary"}>{t("demo.progress", { done: view.position, total: view.steps.length })}</Badge>
          {busy ? <span className="animate-pulse">{t("demo.running")}</span> : null}
        </div>
        {message ? (
          <p className="text-sm" role="status">
            {message}
          </p>
        ) : null}
        <ol className="space-y-3" aria-label={t("demo.script")}>
          {view.steps.map((step, index) => (
            <StepCard
              key={step.key}
              index={index}
              step={step}
              outcome={outcomeOf(step.key)}
              current={view.next?.key === step.key}
              ready={step.needs === "none" || (step.needs === "mailbox" && view.ready.mailbox) || (step.needs === "world" && view.ready.world)}
              canRun={canAct && !!view.started_at && !busy}
              onRun={() => next.mutate(step.key)}
              locale={locale}
            />
          ))}
        </ol>
      </div>
    </div>
  );
}

function StepCard({
  index,
  step,
  outcome,
  current,
  ready,
  canRun,
  onRun,
  locale,
}: {
  index: number;
  step: DemoStep;
  outcome?: DemoStepOutcome;
  current: boolean;
  ready: boolean;
  canRun: boolean;
  onRun: () => void;
  locale: string;
}) {
  const { t } = useI18n();
  const status = outcome?.status;
  return (
    <li className={cn("rounded-lg border bg-card p-3", current ? "border-primary ring-1 ring-primary/40" : "")} aria-current={current ? "step" : undefined} aria-label={`${index + 1}. ${step.title}`}>
      <div className="flex flex-wrap items-start gap-2">
        <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold", status === "done" ? "bg-success/20 text-success-text" : status === "failed" ? "bg-destructive/20 text-destructive" : status === "waiting" ? "bg-warning/20" : "bg-muted")}>
          {status === "done" ? <Check className="h-4 w-4" /> : status === "failed" ? <XCircle className="h-4 w-4" /> : status === "waiting" ? <Clock className="h-4 w-4" /> : index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold">{step.title}</h2>
            {status ? <Badge variant={status === "done" ? "success" : status === "failed" ? "destructive" : "warning"}>{t(`demo.status.${status}`)}</Badge> : null}
            {!ready ? <Badge variant="outline">{t(`demo.needs.${step.needs}`)}</Badge> : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{step.say}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="font-medium">{t("demo.click")}</span> {step.click}
          </p>
          {outcome ? (
            <div className="mt-2 rounded-md bg-muted/40 p-2 text-xs">
              <p>{outcome.summary}</p>
              {(outcome.links ?? []).length ? (
                <div className="mt-1 flex flex-wrap gap-2">
                  {(outcome.links ?? []).map((link) => (
                    <RecordLink key={`${link.label}:${link.path}`} path={link.path} className="text-primary underline-offset-2 hover:underline">
                      {link.label}
                    </RecordLink>
                  ))}
                </div>
              ) : null}
              <p className="mt-1 text-muted-foreground">{formatDateTime(outcome.at, locale)}</p>
            </div>
          ) : null}
        </div>
        {canRun ? (
          <Button size="sm" variant={current ? "default" : "ghost"} onClick={onRun} aria-label={t("demo.run_step", { n: index + 1 })}>
            <Play className="h-4 w-4" /> {current ? t("demo.run") : t("demo.run_again")}
          </Button>
        ) : null}
      </div>
    </li>
  );
}
