/**
 * Talk to the director about a case. Questions are answered from the case's
 * facts; instructions come back as a proposed action with a one-line
 * explanation and a Confirm button (approvers). Nothing runs before that
 * click; the outcome lands in the conversation and on the case timeline.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, MessageSquare, Send, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { cn, formatDate, formatDateTime } from "@/lib/utils";

export type ChatMessage = Schemas["ChatMessage"];

export function useCaseChat(ref: string) {
  return useQuery({
    queryKey: ["cases", "chat", ref],
    queryFn: async () => unwrap(await api.GET("/api/cases/{ref}/chat", { params: { path: { ref } } })),
  });
}

export function CaseChat({ caseRef, compact = false }: { caseRef: string; compact?: boolean }) {
  const { t, locale } = useI18n();
  const { hasRole, session } = useAuth();
  const queryClient = useQueryClient();
  const chat = useCaseChat(caseRef);
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["cases", "chat", caseRef] });
    void queryClient.invalidateQueries({ queryKey: ["cases"] });
  };

  const ask = useMutation({
    mutationFn: async (question: string) =>
      unwrap(await api.POST("/api/cases/{ref}/chat", { params: { path: { ref: caseRef } }, body: { text: question } })),
    onSuccess: () => {
      setText("");
      refresh();
    },
  });
  const confirm = useMutation({
    mutationFn: async (messageId: number) =>
      unwrap(
        await api.POST("/api/cases/{ref}/chat/{message_id}/confirm", { params: { path: { ref: caseRef, message_id: messageId } } }),
      ),
    onSuccess: refresh,
  });
  const dismiss = useMutation({
    mutationFn: async (messageId: number) =>
      unwrap(
        await api.POST("/api/cases/{ref}/chat/{message_id}/dismiss", { params: { path: { ref: caseRef, message_id: messageId } } }),
      ),
    onSuccess: refresh,
  });

  const messages = chat.data ?? [];
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "nearest" });
  }, [messages.length, ask.isPending]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = text.trim();
    if (question && !ask.isPending) ask.mutate(question);
  };
  const busy = ask.isPending || confirm.isPending || dismiss.isPending;
  const error = ask.error ?? confirm.error ?? dismiss.error;

  return (
    <section aria-label={t("chat.title")} className={cn("flex flex-col rounded-lg border bg-card", compact ? "" : "h-full")}>
      <header className="flex items-center gap-2 border-b px-3 py-2 text-sm font-semibold">
        <MessageSquare className="h-4 w-4" /> {t("chat.title")}
      </header>
      <div className={cn("flex flex-col gap-2 overflow-y-auto p-3", compact ? "max-h-72" : "flex-1")} role="log" aria-live="polite">
        {messages.length === 0 && !ask.isPending ? <p className="text-xs text-muted-foreground">{t("chat.empty")}</p> : null}
        {messages.map((message) => (
          <Bubble
            key={message.id}
            message={message}
            mine={message.by === session?.user.email}
            canDecide={hasRole("approver") && message.action_status === "proposed"}
            busy={busy}
            onConfirm={() => confirm.mutate(message.id)}
            onDismiss={() => dismiss.mutate(message.id)}
            locale={locale}
          />
        ))}
        {ask.isPending ? <p className="text-xs text-muted-foreground">{t("chat.thinking")}</p> : null}
        {error ? (
          <p className="text-xs text-destructive" role="alert">
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : null}
        <div ref={bottom} />
      </div>
      <form className="flex items-end gap-2 border-t p-2" onSubmit={submit}>
        <Textarea
          aria-label={t("chat.input")}
          placeholder={t("chat.placeholder")}
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
  );
}

function Bubble({
  message,
  mine,
  canDecide,
  busy,
  onConfirm,
  onDismiss,
  locale,
}: {
  message: ChatMessage;
  mine: boolean;
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
      <div
        className={cn(
          "max-w-[85%] whitespace-pre-line rounded-lg px-3 py-2 text-sm",
          director ? "bg-muted" : mine ? "bg-primary text-primary-foreground" : "bg-accent",
        )}
      >
        {message.text}
      </div>
      {message.action ? (
        <div className="max-w-[85%] rounded-md border border-primary/40 bg-primary/5 p-2 text-xs" data-testid="proposed-action">
          <div className="mb-1 flex items-center gap-2">
            <Badge variant="outline">{t(`chat.action.${message.action.kind}`)}</Badge>
            {message.action_status === "confirmed" ? <Badge variant="success">{t("chat.confirmed")}</Badge> : null}
            {message.action_status === "dismissed" ? <Badge variant="secondary">{t("chat.dismissed")}</Badge> : null}
          </div>
          <p>{message.action.explanation}</p>
          {message.action.until ? <p className="text-muted-foreground">{formatDate(message.action.until, locale)}</p> : null}
          {canDecide ? (
            <div className="mt-2 flex gap-2">
              <Button size="sm" onClick={onConfirm} disabled={busy}>
                <Check className="h-3 w-3" /> {t("chat.confirm")}
              </Button>
              <Button size="sm" variant="outline" onClick={onDismiss} disabled={busy}>
                <X className="h-3 w-3" /> {t("chat.dismiss")}
              </Button>
            </div>
          ) : null}
        </div>
      ) : null}
      <span className="text-[10px] text-muted-foreground">
        {director ? t("chat.director") : (message.by ?? t("chat.someone"))} · {formatDateTime(message.at, locale)}
      </span>
    </div>
  );
}
