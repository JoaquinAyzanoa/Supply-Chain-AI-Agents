/**
 * The morning briefing: the director's one paragraph and the sections behind it, every
 * item a link into the place where it is decided. Approvers can build today's briefing
 * now and email it to the buyers.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, RefreshCw, Sunrise } from "lucide-react";
import { useState } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { RecordLink } from "@/components/RecordLink";
import { ApiError } from "@/api/client";
import { useI18n } from "@/i18n";
import { formatDate, formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

export type Briefing = Schemas["Briefing"];
export type BriefingSection = Schemas["BriefingSection"];

export function useBriefing(day?: string) {
  return useQuery({
    queryKey: ["briefing", day ?? "latest"],
    queryFn: async () => {
      const result = await api.GET("/api/briefing", { params: { query: day ? { day } : {} } });
      if (result.response.status === 404) return null;
      return unwrap(result) as Briefing;
    },
  });
}

export function BriefingPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const [day, setDay] = useState<string | undefined>(undefined);
  const briefing = useBriefing(day);
  const history = useQuery({
    queryKey: ["briefing", "history"],
    queryFn: async () => unwrap(await api.GET("/api/briefing/history", { params: { query: { limit: 14 } } })) as Briefing[],
  });
  const [recipients, setRecipients] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["briefing"] });
  const build = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/briefing/run")) as Briefing,
    onSuccess: () => {
      setDay(undefined);
      setMessage(t("briefing.built"));
      refresh();
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : String(error)),
  });
  const email = useMutation({
    mutationFn: async () => {
      const to = recipients
        .split(/[,\s]+/)
        .map((v) => v.trim())
        .filter(Boolean);
      return unwrap(await api.POST("/api/briefing/email", { body: { to } })) as Schemas["EmailResponse"];
    },
    onSuccess: (sent) => {
      setMessage(t("briefing.emailed", { to: sent.sent_to.join(", ") }));
      refresh();
    },
    onError: (error) => setMessage(error instanceof ApiError ? error.message : String(error)),
  });

  if (briefing.isPending) return <Loading />;
  if (briefing.error) return <ErrorBox error={briefing.error} onRetry={() => briefing.refetch()} />;
  const data = briefing.data;
  return (
    <div>
      <PageTitle title={t("briefing.title")}>
        <div className="flex flex-wrap items-center gap-2">
          {history.data && history.data.length > 1 ? (
            <select aria-label={t("briefing.pick_day")} className="h-8 rounded-md border bg-card px-2 text-sm" value={day ?? history.data[0]?.day ?? ""} onChange={(e) => setDay(e.target.value)}>
              {history.data.map((b) => (
                <option key={b.day} value={b.day}>
                  {formatDate(b.day, locale)}
                </option>
              ))}
            </select>
          ) : null}
          {canAct ? (
            <Button size="sm" variant="outline" onClick={() => build.mutate()} disabled={build.isPending}>
              <RefreshCw className="h-4 w-4" /> {t("briefing.build_now")}
            </Button>
          ) : null}
        </div>
      </PageTitle>
      <div className="space-y-6 p-4">
        {message ? (
          <p className="text-sm text-muted-foreground" role="status">
            {message}
          </p>
        ) : null}
        {!data ? (
          <Empty text={canAct ? t("briefing.none_yet_build") : t("briefing.none_yet")} />
        ) : (
          <>
            <section className="rounded-lg border bg-card p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <Sunrise className="h-4 w-4" />
                <span>{t("briefing.for_day", { day: formatDate(data.day, locale) })}</span>
                {data.created_at ? <span>· {formatDateTime(data.created_at, locale)}</span> : null}
                {data.emailed_to.length ? <Badge variant="secondary">{t("briefing.emailed_to", { n: data.emailed_to.length })}</Badge> : null}
              </div>
              {data.paragraph ? <p className="text-base leading-relaxed">{data.paragraph}</p> : <p className="text-sm italic text-muted-foreground">{t("briefing.no_paragraph")}</p>}
            </section>
            <div className="grid gap-4 lg:grid-cols-2">
              {data.sections.map((section) => (
                <Section key={section.key} section={section} />
              ))}
            </div>
            {canAct ? (
              <section className="flex flex-wrap items-end gap-2 rounded-md border bg-card p-3" aria-label={t("briefing.email_title")}>
                <div className="flex min-w-64 flex-1 flex-col gap-1">
                  <label htmlFor="briefing-to" className="text-sm font-medium">
                    {t("briefing.email_to")}
                  </label>
                  <Input id="briefing-to" value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder={t("briefing.email_hint")} />
                </div>
                <Button size="sm" onClick={() => email.mutate()} disabled={email.isPending}>
                  <Mail className="h-4 w-4" /> {t("briefing.email_send")}
                </Button>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function Section({ section }: { section: BriefingSection }) {
  const { t } = useI18n();
  return (
    <section className="rounded-lg border bg-card p-3" aria-label={section.title}>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        {section.title}
        <Badge variant={section.count && (section.key === "needs_you" || section.key === "risks" || section.key === "late") ? "warning" : "secondary"}>{section.count}</Badge>
      </h2>
      {section.items.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("briefing.nothing")}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {section.items.map((item, index) => (
            <li key={index}>
              {item.path ? (
                <RecordLink path={item.path} className="text-primary underline">
                  {item.text}
                </RecordLink>
              ) : (
                item.text
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
