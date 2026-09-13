/**
 * Talk to the director about the whole department. Answers cite the records they rest
 * on (each a link); an instruction comes back as a plan of steps an approver confirms
 * once, after which every step runs through the agents and their approvals.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { Bot, Check, Send, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { RecordLink } from "@/components/RecordLink";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export type AssistantMessage = Schemas["AssistantMessage"];
export type PlanStep = Schemas["PlanStep"];

export function useAssistant() {
  return useQuery({
    queryKey: ["assistant"],
    queryFn: async () => unwrap(await api.GET("/api/assistant")) as AssistantMessage[],
  });
}

export function AssistantPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const chat = useAssistant();
  const search = useSearch({ strict: false }) as { q?: string };
  const [text, setText] = useState(search.q ?? "");
  const bottom = useRef<HTMLDivElement>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["assistant"] });
    void queryClient.invalidateQueries({ queryKey: ["approvals"] });
    void queryClient.invalidateQueries({ queryKey: ["board"] });
    void queryClient.invalidateQueries({ queryKey: ["cases"] });
    void queryClient.invalidateQueries({ queryKey: ["sourcing"] });
  };
  const ask = useMutation({
    mutationFn: async (question: string) => unwrap(await api.POST("/api/assistant", { body: { text: question } })),
    onSuccess: refresh,
    onError: (_error, question) => setText((current) => current || question),
  });
  const confirm = useMutation({
    mutationFn: async (messageId: number) => unwrap(await api.POST("/api/assistant/{message_id}/confirm", { params: { path: { message_id: messageId } } })),
    onSuccess: refresh,
  });
  const dismiss = useMutation({
    mutationFn: async (messageId: number) => unwrap(await api.POST("/api/assistant/{message_id}/dismiss", { params: { path: { message_id: messageId } } })),
    onSuccess: refresh,
  });
  const messages = chat.data ?? [];
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "nearest" });
  }, [messages.length, ask.isPending]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = text.trim();
    if (!question || ask.isPending) return;
    setText("");
    ask.mutate(question);
  };
  const pendingQuestion = ask.isPending ? ask.variables : undefined;
  const busy = ask.isPending || confirm.isPending || dismiss.isPending;
  const error = ask.error ?? confirm.error ?? dismiss.error;
  return (
    <div className="flex h-full flex-col">
      <PageTitle title={t("assistant.title")} />
      <section aria-label={t("assistant.title")} className="m-4 flex min-h-[60vh] flex-1 flex-col rounded-lg border bg-card">
        <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3" role="log" aria-live="polite">
          {messages.length === 0 && !ask.isPending ? <p className="max-w-2xl text-sm text-muted-foreground">{t("assistant.empty")}</p> : null}
          {messages.map((message) => (
            <Bubble
              key={message.id}
              message={message}
              canDecide={hasRole("approver") && message.plan_status === "proposed"}
              busy={busy}
              onConfirm={() => confirm.mutate(message.id)}
              onDismiss={() => dismiss.mutate(message.id)}
              locale={locale}
            />
          ))}
          {pendingQuestion !== undefined ? (
            <div className="flex flex-col items-end gap-1">
              <div className="max-w-[85%] whitespace-pre-line rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{pendingQuestion}</div>
            </div>
          ) : null}
          {ask.isPending ? <p className="text-xs text-muted-foreground">{t("assistant.thinking")}</p> : null}
          {error ? (
            <p className="text-xs text-destructive" role="alert">
              {error instanceof Error ? error.message : String(error)}
            </p>
          ) : null}
          <div ref={bottom} />
        </div>
        <form className="flex items-end gap-2 border-t p-2" onSubmit={submit}>
          <Textarea
            aria-label={t("assistant.input")}
            placeholder={t("assistant.placeholder")}
            rows={2}
            className="min-h-0"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) submit(event);
            }}
          />
          <Button type="submit" size="icon" aria-label={t("chat.send")} disabled={busy || !text.trim()}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </section>
    </div>
  );
}

function Bubble({
  message,
  canDecide,
  busy,
  onConfirm,
  onDismiss,
  locale,
}: {
  message: AssistantMessage;
  canDecide: boolean;
  busy: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
  locale: string;
}) {
  const { t } = useI18n();
  const director = message.role === "director";
  return (
    <div className={cn("flex flex-col gap-1", director ? "items-start" : "items-end")}>
      <div className={cn("max-w-[85%] whitespace-pre-line rounded-lg px-3 py-2 text-sm", director ? "bg-muted" : "bg-primary text-primary-foreground")}>{message.text}</div>
      {message.citations.length ? (
        <div className="flex max-w-[85%] flex-wrap gap-1" aria-label={t("assistant.sources")}>
          {message.citations.map((citation) => (
            <RecordLink key={citation.ref} path={citation.path} className="rounded-full border px-2 py-0.5 text-xs text-primary hover:bg-accent">
              {citation.ref}
            </RecordLink>
          ))}
        </div>
      ) : null}
      {message.plan ? (
        <div className="max-w-[85%] rounded-md border border-primary/40 bg-primary/5 p-2 text-xs" data-testid="proposed-plan">
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="outline">{t("assistant.plan")}</Badge>
            {message.plan_status === "confirmed" ? <Badge variant="success">{t("chat.confirmed")}</Badge> : null}
            {message.plan_status === "dismissed" ? <Badge variant="secondary">{t("chat.dismissed")}</Badge> : null}
          </div>
          <p className="font-medium">{message.plan.summary}</p>
          <ol className="mt-1 list-decimal space-y-1 pl-4">
            {message.plan.steps.map((step, index) => (
              <li key={index}>
                <span className="font-medium">{t(`assistant.step.${step.kind}`)}</span>
                {step.po_name ? ` · ${step.po_name}` : ""}
                {step.product_ref ? ` · ${step.product_ref}` : ""}
                {step.qty ? ` × ${step.qty}` : ""}
                {step.until ? ` · ${formatDate(step.until, locale)}` : ""}
                {step.playbook ? ` · ${step.playbook}` : ""}
                <div className="text-muted-foreground">{step.explanation}</div>
              </li>
            ))}
          </ol>
          {message.outcome ? <pre className="mt-2 whitespace-pre-wrap rounded bg-muted p-2 text-[11px]">{message.outcome}</pre> : null}
          {canDecide ? (
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={onConfirm} disabled={busy}>
                <Check className="h-3 w-3" /> {t("assistant.confirm")}
              </Button>
              <Button size="sm" variant="outline" onClick={onDismiss} disabled={busy}>
                <X className="h-3 w-3" /> {t("chat.dismiss")}
              </Button>
            </div>
          ) : null}
          {message.plan_status === "proposed" && !canDecide ? <p className="mt-1 text-muted-foreground">{t("assistant.needs_approver")}</p> : null}
        </div>
      ) : null}
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
        {director ? (
          <>
            <Bot className="h-3 w-3" /> {t("chat.director")}
          </>
        ) : (
          (message.by ?? t("chat.someone"))
        )}{" "}
        · {formatDateTime(message.at, locale)}
      </span>
    </div>
  );
}
