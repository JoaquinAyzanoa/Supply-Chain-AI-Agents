/**
 * Runtime settings (admins): the model per agent from the registry, the
 * follow-up policy and the planning defaults. What runs without approval is
 * the autonomy policy, on its own page. Every save is a new version the services pick up
 * within a minute; the history shows who changed what and when.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Save } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { api, unwrap, type Schemas } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { agentName } from "@/features/cases/labels";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";

type RuntimeSettings = Schemas["RuntimeSettings"];
type ModelOption = Schemas["ModelOption"];

const AGENTS = ["supplier_comms", "inventory_planning", "director"] as const;

export function useSettings() {
  return useQuery({ queryKey: ["settings", "current"], queryFn: async () => unwrap(await api.GET("/api/settings")) });
}

export function useSettingsHistory() {
  return useQuery({ queryKey: ["settings", "history"], queryFn: async () => unwrap(await api.GET("/api/settings/history")) });
}

export function useModelOptions() {
  return useQuery({ queryKey: ["settings", "models"], queryFn: async () => unwrap(await api.GET("/api/settings/models")) });
}

/** Form state keeps lists as text so half-typed input never breaks the form. */
interface Draft {
  model_by_agent: Record<string, string>;
  rfq_no_reply_days: string;
  po_eta_request_before_days: string;
  po_late_days: string;
  approval_stale_days: string;
  approval_expire_days: string;
  max_actions_per_run: string;
  ignored_senders: string;
  internal_senders: string;
  planning_service_level: string;
  planning_review_period_days: string;
  planning_max_coverage_days: string;
  holding_cost_pct_year: string;
  sourcing_top_n: string;
  sourcing_deadline_days: string;
  sourcing_freight_pct: string;
  negotiation_cap_pct: string;
  negotiation_max_rounds: string;
}

function toDraft(settings: RuntimeSettings): Draft {
  return {
    model_by_agent: { ...(settings.model_by_agent ?? {}) },
    rfq_no_reply_days: settings.rfq_no_reply_days.join(", "),
    po_eta_request_before_days: String(settings.po_eta_request_before_days),
    po_late_days: settings.po_late_days.join(", "),
    approval_stale_days: String(settings.approval_stale_days),
    approval_expire_days: String(settings.approval_expire_days),
    max_actions_per_run: String(settings.max_actions_per_run),
    ignored_senders: (settings.ignored_senders ?? []).join(", "),
    internal_senders: (settings.internal_senders ?? []).join(", "),
    planning_service_level: settings.planning_service_level === null || settings.planning_service_level === undefined ? "" : String(settings.planning_service_level),
    planning_review_period_days: settings.planning_review_period_days === null || settings.planning_review_period_days === undefined ? "" : String(settings.planning_review_period_days),
    planning_max_coverage_days: settings.planning_max_coverage_days === null || settings.planning_max_coverage_days === undefined ? "" : String(settings.planning_max_coverage_days),
    holding_cost_pct_year: String(settings.holding_cost_pct_year ?? 20),
    sourcing_top_n: String(settings.sourcing_top_n ?? 3),
    sourcing_deadline_days: String(settings.sourcing_deadline_days ?? 5),
    sourcing_freight_pct: String(settings.sourcing_freight_pct ?? 5),
    negotiation_cap_pct: String(settings.negotiation_cap_pct ?? 10),
    negotiation_max_rounds: String(settings.negotiation_max_rounds ?? 2),
  };
}

const ints = (text: string): number[] =>
  text
    .split(/[,\s]+/)
    .filter(Boolean)
    .map((v) => Number(v));
const intOrNull = (text: string): number | null => (text.trim() === "" ? null : Number(text));

/** The edited fields on top of the current settings, so fields this form does not
 *  show (the autonomy policy, older lists) survive a save unchanged. */
export function fromDraft(draft: Draft, base: RuntimeSettings): RuntimeSettings {
  return {
    ...base,
    model_by_agent: Object.fromEntries(Object.entries(draft.model_by_agent).filter(([, v]) => v)),
    rfq_no_reply_days: ints(draft.rfq_no_reply_days),
    po_eta_request_before_days: Number(draft.po_eta_request_before_days),
    po_late_days: ints(draft.po_late_days),
    approval_stale_days: Number(draft.approval_stale_days),
    approval_expire_days: Number(draft.approval_expire_days),
    max_actions_per_run: Number(draft.max_actions_per_run),
    ignored_senders: draft.ignored_senders
      .split(/[,\s]+/)
      .map((v) => v.trim())
      .filter(Boolean),
    internal_senders: draft.internal_senders
      .split(/[,\s]+/)
      .map((v) => v.trim())
      .filter(Boolean),
    planning_service_level: intOrNull(draft.planning_service_level),
    planning_review_period_days: intOrNull(draft.planning_review_period_days),
    planning_max_coverage_days: intOrNull(draft.planning_max_coverage_days),
    holding_cost_pct_year: Number(draft.holding_cost_pct_year),
    sourcing_top_n: Number(draft.sourcing_top_n),
    sourcing_deadline_days: Number(draft.sourcing_deadline_days),
    sourcing_freight_pct: Number(draft.sourcing_freight_pct),
    negotiation_cap_pct: Number(draft.negotiation_cap_pct),
    negotiation_max_rounds: Number(draft.negotiation_max_rounds),
  };
}

export function SettingsPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const current = useSettings();
  const history = useSettingsHistory();
  const models = useModelOptions();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (current.data) setDraft(toDraft(current.data.settings));
  }, [current.data]);

  const save = useMutation({
    mutationFn: async (settings: RuntimeSettings) => unwrap(await api.PUT("/api/settings", { body: { settings, note: note || null } })),
    onSuccess: (saved) => {
      setMessage(t("settings.saved", { version: saved.version }));
      setNote("");
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : String(error)),
  });

  if (!hasRole("admin")) return <p className="p-4 text-sm text-muted-foreground">{t("settings.forbidden")}</p>;
  if (current.isPending || !draft) return <Loading />;
  if (current.error) return <ErrorBox error={current.error} onRetry={() => current.refetch()} />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setMessage(null);
    save.mutate(fromDraft(draft, current.data.settings));
  };
  const field = (key: Exclude<keyof Draft, "model_by_agent">, label: string, hint?: string, type = "text") => (
    <div className="flex flex-col gap-1">
      <Label htmlFor={`s-${key}`}>{label}</Label>
      <Input id={`s-${key}`} type={type} value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  );

  return (
    <div>
      <PageTitle title={t("settings.title")}>
        <span className="text-xs text-muted-foreground">
          {t("settings.version", { version: current.data.version, by: current.data.changed_by, at: formatDateTime(current.data.changed_at, locale) })}
        </span>
      </PageTitle>
      <form className="grid gap-6 p-4 lg:grid-cols-2" onSubmit={submit}>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.models")}</h2>
          <p className="text-xs text-muted-foreground">{t("settings.models_hint")}</p>
          {AGENTS.map((agent) => (
            <div key={agent} className="flex flex-col gap-1">
              <Label htmlFor={`m-${agent}`}>{agentName(t, agent)}</Label>
              <select
                id={`m-${agent}`}
                className="h-9 rounded-md border bg-card px-2 text-sm"
                value={draft.model_by_agent[agent] ?? ""}
                onChange={(e) => setDraft({ ...draft, model_by_agent: { ...draft.model_by_agent, [agent]: e.target.value } })}
              >
                <option value="">{t("settings.model_default")}</option>
                {(models.data ?? []).map((option: ModelOption) => (
                  <option key={option.name} value={option.name} disabled={!option.configured}>
                    {option.name} · {option.provider} · ${option.input_usd_per_mtok}/{option.output_usd_per_mtok} per Mtok
                    {option.reasoning ? " · reasoning" : ""}
                    {option.configured ? "" : ` · ${t("settings.model_unconfigured")}`}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.policy")}</h2>
          {field("rfq_no_reply_days", t("settings.f.rfq_no_reply_days"), t("settings.h.rfq_no_reply_days"))}
          {field("po_eta_request_before_days", t("settings.f.po_eta_request_before_days"), undefined, "number")}
          {field("po_late_days", t("settings.f.po_late_days"), t("settings.h.po_late_days"))}
          {field("approval_stale_days", t("settings.f.approval_stale_days"), undefined, "number")}
          {field("approval_expire_days", t("settings.f.approval_expire_days"), undefined, "number")}
          {field("max_actions_per_run", t("settings.f.max_actions_per_run"), undefined, "number")}
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.auto_send")}</h2>
          <p className="text-xs text-muted-foreground">{t("settings.auto_send_moved")}</p>
          <Link to="/autonomy" className="text-sm text-primary underline">
            {t("nav.autonomy")}
          </Link>
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.mailbox")}</h2>
          {field("ignored_senders", t("settings.f.ignored_senders"), t("settings.h.ignored_senders"))}
          {field("internal_senders", t("settings.f.internal_senders"), t("settings.h.internal_senders"))}
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.planning")}</h2>
          <p className="text-xs text-muted-foreground">{t("settings.planning_hint")}</p>
          {field("planning_service_level", t("settings.f.planning_service_level"))}
          {field("planning_review_period_days", t("settings.f.planning_review_period_days"))}
          {field("planning_max_coverage_days", t("settings.f.planning_max_coverage_days"))}
          {field("holding_cost_pct_year", t("settings.f.holding_cost_pct_year"), t("settings.h.holding_cost_pct_year"), "number")}
        </section>
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold">{t("settings.sourcing")}</h2>
          <p className="text-xs text-muted-foreground">{t("settings.sourcing_hint")}</p>
          {field("sourcing_top_n", t("settings.f.sourcing_top_n"), undefined, "number")}
          {field("sourcing_deadline_days", t("settings.f.sourcing_deadline_days"), undefined, "number")}
          {field("sourcing_freight_pct", t("settings.f.sourcing_freight_pct"), undefined, "number")}
          {field("negotiation_cap_pct", t("settings.f.negotiation_cap_pct"), t("settings.h.negotiation_cap_pct"), "number")}
          {field("negotiation_max_rounds", t("settings.f.negotiation_max_rounds"), undefined, "number")}
        </section>
        <div className="flex flex-wrap items-end gap-3 lg:col-span-2">
          <div className="flex min-w-64 flex-1 flex-col gap-1">
            <Label htmlFor="s-note">{t("settings.note")}</Label>
            <Input id="s-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
          </div>
          <Button type="submit" disabled={save.isPending}>
            <Save className="h-4 w-4" /> {t("settings.save")}
          </Button>
          {message ? (
            <span className="text-sm text-muted-foreground" role="status">
              {message}
            </span>
          ) : null}
        </div>
      </form>
      <section className="p-4">
        <h2 className="mb-2 text-sm font-semibold">{t("settings.history")}</h2>
        {history.data && history.data.length === 0 ? <p className="text-sm text-muted-foreground">{t("settings.no_history")}</p> : null}
        {history.data && history.data.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("settings.col.version")}</TableHead>
                <TableHead>{t("settings.col.when")}</TableHead>
                <TableHead>{t("settings.col.who")}</TableHead>
                <TableHead>{t("settings.col.note")}</TableHead>
                <TableHead>{t("settings.col.models")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {history.data.map((version) => (
                <TableRow key={version.version}>
                  <TableCell>
                    <Badge variant="secondary">v{version.version}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(version.changed_at, locale)}</TableCell>
                  <TableCell>{version.changed_by}</TableCell>
                  <TableCell>{version.note ?? ""}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {Object.entries(version.settings.model_by_agent ?? {})
                      .map(([agent, model]) => `${agent}: ${model}`)
                      .join(", ") || t("settings.model_default")}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </section>
    </div>
  );
}
