/**
 * Autonomy: the rules that decide which proposed actions run alone. Everyone
 * can read them and replay the last weeks of approvals against a draft;
 * admins edit. A change that only lowers autonomy is saved at once; one that
 * widens it becomes an approval a second person confirms. The page also shows
 * the "done automatically" feed and the versions.
 */
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowUp, FlaskConical, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSettingsHistory } from "@/features/settings/SettingsPage";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { DecisionStats, Suggestions } from "@/features/learning/Suggestions";
import { AutoActionsFeed } from "./AutoActionsFeed";
import { EMAIL_KINDS, LEVELS, RULE_KINDS, usePolicy, usePreview, useSavePolicy, type AutonomyPolicy, type AutonomyRule, type PreviewResponse, type RuleConditions } from "./api";

/** Form state keeps numbers as text so half-typed input never breaks the form. */
interface RuleDraft {
  id: string;
  kind: string;
  level: string;
  revert_hours: string;
  note: string;
  enabled: boolean;
  partner_ids: string;
  email_kinds: string[];
  amount_max: string;
  supplier_score_min: string;
  confidence_min: string;
  change_pct_max: string;
  change_days_max: string;
  days_late_max: string;
  first_time_supplier: "any" | "yes" | "no";
}

const NUMBER_CONDITIONS = ["amount_max", "supplier_score_min", "confidence_min", "change_pct_max", "change_days_max", "days_late_max"] as const;
type NumberCondition = (typeof NUMBER_CONDITIONS)[number];

function toDraft(rule: AutonomyRule): RuleDraft {
  const when: RuleConditions = rule.when ?? { partner_ids: [], email_kinds: [] };
  const text = (value: number | null | undefined) => (value === null || value === undefined ? "" : String(value));
  return {
    id: rule.id,
    kind: rule.kind ?? "*",
    level: rule.level ?? "approve",
    revert_hours: String(rule.revert_hours ?? 24),
    note: rule.note ?? "",
    enabled: rule.enabled ?? true,
    partner_ids: (when.partner_ids ?? []).join(", "),
    email_kinds: [...(when.email_kinds ?? [])],
    amount_max: text(when.amount_max),
    supplier_score_min: text(when.supplier_score_min),
    confidence_min: text(when.confidence_min),
    change_pct_max: text(when.change_pct_max),
    change_days_max: text(when.change_days_max),
    days_late_max: text(when.days_late_max),
    first_time_supplier: when.first_time_supplier === true ? "yes" : when.first_time_supplier === false ? "no" : "any",
  };
}

const numberOrNull = (text: string): number | null => (text.trim() === "" ? null : Number(text));

export function fromDrafts(drafts: RuleDraft[]): AutonomyPolicy {
  return {
    rules: drafts.map((d) => ({
      id: d.id.trim(),
      kind: d.kind,
      level: d.level as AutonomyRule["level"],
      revert_hours: Number(d.revert_hours || 24),
      note: d.note,
      enabled: d.enabled,
      when: {
        partner_ids: d.partner_ids
          .split(/[,\s]+/)
          .filter(Boolean)
          .map((v) => Number(v)),
        email_kinds: [...d.email_kinds],
        amount_max: numberOrNull(d.amount_max),
        supplier_score_min: numberOrNull(d.supplier_score_min),
        confidence_min: numberOrNull(d.confidence_min),
        change_pct_max: numberOrNull(d.change_pct_max),
        change_days_max: numberOrNull(d.change_days_max),
        days_late_max: numberOrNull(d.days_late_max),
        first_time_supplier: d.first_time_supplier === "any" ? null : d.first_time_supplier === "yes",
      },
    })),
  };
}

function newRule(n: number): RuleDraft {
  return toDraft({ id: `rule-${n}`, kind: "send_email", level: "auto_notice", revert_hours: 24, note: "", enabled: true, when: { partner_ids: [], email_kinds: [] } });
}

export function AutonomyPage() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const policy = usePolicy();
  const history = useSettingsHistory();
  const preview = usePreview();
  const save = useSavePolicy();
  const [drafts, setDrafts] = useState<RuleDraft[] | null>(null);
  const [note, setNote] = useState("");
  const [days, setDays] = useState(30);
  const [feedDays, setFeedDays] = useState(7);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (policy.data) setDrafts(policy.data.policy.rules.map(toDraft));
  }, [policy.data]);

  if (policy.isPending || !drafts) return <Loading />;
  if (policy.error) return <ErrorBox error={policy.error} onRetry={() => policy.refetch()} />;
  const canEdit = hasRole("admin");
  const pending = policy.data.pending;

  const update = (index: number, patch: Partial<RuleDraft>) => setDrafts(drafts.map((d, i) => (i === index ? { ...d, ...patch } : d)));
  const move = (index: number, delta: number) => {
    const next = [...drafts];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target]!, next[index]!];
    setDrafts(next);
  };
  const runPreview = () => {
    setMessage(null);
    preview.mutate({ policy: fromDrafts(drafts), days });
  };
  const submit = async () => {
    setMessage(null);
    try {
      const result = await save.mutateAsync({ policy: fromDrafts(drafts), note: note || null });
      setNote("");
      if (result.approval_id) setMessage(t("autonomy.needs_second", { id: result.approval_id, rules: result.widened.join(", ") }));
      else setMessage(t("autonomy.saved", { version: result.saved?.version ?? "?" }));
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div>
      <PageTitle title={t("autonomy.title")}>
        <span className="text-xs text-muted-foreground">
          {t("settings.version", { version: policy.data.version, by: policy.data.changed_by, at: formatDateTime(policy.data.changed_at, locale) })}
        </span>
      </PageTitle>
      <div className="flex flex-col gap-6 p-4">
        <p className="text-sm text-muted-foreground">{t("autonomy.intro")}</p>
        {pending ? (
          <div className="rounded-md border border-warning bg-warning/10 p-3 text-sm" role="status">
            {t("autonomy.pending", { id: pending.approval_id, by: pending.requested_by, rules: pending.widened.join(", ") })}{" "}
            <Link to="/approvals" search={{ id: pending.approval_id }} className="text-primary underline">
              {t("autonomy.pending_link")}
            </Link>
          </div>
        ) : null}

        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">{t("learning.suggestions")}</h2>
          <p className="text-xs text-muted-foreground">{t("learning.suggestions_hint")}</p>
          <Suggestions />
        </section>

        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">{t("autonomy.rules")}</h2>
            {canEdit ? (
              <Button variant="outline" size="sm" onClick={() => setDrafts([...drafts, newRule(drafts.length + 1)])}>
                <Plus className="h-4 w-4" /> {t("autonomy.add_rule")}
              </Button>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">{t("autonomy.rules_hint")}</p>
          {drafts.length === 0 ? <p className="text-sm text-muted-foreground">{t("autonomy.no_rules")}</p> : null}
          {drafts.map((rule, index) => (
            <RuleEditor
              key={index}
              index={index}
              rule={rule}
              editable={canEdit}
              onChange={(patch) => update(index, patch)}
              onMove={(delta) => move(index, delta)}
              onRemove={() => setDrafts(drafts.filter((_, i) => i !== index))}
            />
          ))}
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="a-days">{t("autonomy.preview_days")}</Label>
              <Input id="a-days" type="number" min={1} max={365} className="w-24" value={days} onChange={(e) => setDays(Number(e.target.value) || 30)} />
            </div>
            <Button variant="outline" onClick={runPreview} disabled={preview.isPending}>
              <FlaskConical className="h-4 w-4" /> {preview.isPending ? t("autonomy.previewing") : t("autonomy.preview")}
            </Button>
            {canEdit ? (
              <>
                <div className="flex min-w-64 flex-1 flex-col gap-1">
                  <Label htmlFor="a-note">{t("settings.note")}</Label>
                  <Input id="a-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
                </div>
                <Button onClick={submit} disabled={save.isPending || Boolean(pending)}>
                  <Save className="h-4 w-4" /> {t("autonomy.save")}
                </Button>
              </>
            ) : null}
          </div>
          {message ? (
            <p className="text-sm text-muted-foreground" role="status">
              {message}
            </p>
          ) : null}
          {preview.error ? <p className="text-sm text-destructive">{preview.error.message}</p> : null}
          {preview.data ? <PreviewResult result={preview.data} /> : null}
        </section>

        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">{t("autonomy.feed.title")}</h2>
            <select aria-label={t("autonomy.feed.days")} className="h-8 rounded-md border bg-card px-2 text-sm" value={feedDays} onChange={(e) => setFeedDays(Number(e.target.value))}>
              {[1, 7, 30, 90].map((d) => (
                <option key={d} value={d}>
                  {t("autonomy.feed.last_days", { n: d })}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-muted-foreground">{t("autonomy.feed.hint")}</p>
          <AutoActionsFeed days={feedDays} />
        </section>

        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">{t("learning.stats")}</h2>
          <DecisionStats />
        </section>

        <section>
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
                  <TableHead>{t("autonomy.col.rules")}</TableHead>
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
                      {(version.settings.autonomy?.rules ?? []).map((r) => `${r.id} → ${t(`autonomy.level.${r.level ?? "approve"}`)}`).join(", ") || t("autonomy.no_rules")}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function RuleEditor({
  index,
  rule,
  editable,
  onChange,
  onMove,
  onRemove,
}: {
  index: number;
  rule: RuleDraft;
  editable: boolean;
  onChange: (patch: Partial<RuleDraft>) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const id = (field: string) => `r${index}-${field}`;
  const number = (field: NumberCondition, label: string, step = "any") => (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id(field)}>{label}</Label>
      <Input id={id(field)} type="number" step={step} min={0} className="h-8" value={rule[field]} disabled={!editable} onChange={(e) => onChange({ [field]: e.target.value })} />
    </div>
  );
  return (
    <div className={`rounded-md border bg-card p-3 ${rule.enabled ? "" : "opacity-60"}`} data-testid={`rule-${index}`} aria-label={t("autonomy.rule_n", { n: index + 1 })}>
      <div className="grid gap-2 md:grid-cols-[1fr_1fr_1fr_6rem_auto]">
        <div className="flex flex-col gap-1">
          <Label htmlFor={id("id")}>{t("autonomy.f.id")}</Label>
          <Input id={id("id")} className="h-8" value={rule.id} disabled={!editable} onChange={(e) => onChange({ id: e.target.value })} />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id("kind")}>{t("autonomy.f.kind")}</Label>
          <select id={id("kind")} className="h-8 rounded-md border bg-card px-2 text-sm" value={rule.kind} disabled={!editable} onChange={(e) => onChange({ kind: e.target.value })}>
            {RULE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind === "*" ? t("autonomy.any_kind") : t(`approvals.kind.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id("level")}>{t("autonomy.f.level")}</Label>
          <select id={id("level")} className="h-8 rounded-md border bg-card px-2 text-sm" value={rule.level} disabled={!editable} onChange={(e) => onChange({ level: e.target.value })}>
            {LEVELS.map((level) => (
              <option key={level} value={level}>
                {t(`autonomy.level.${level}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={id("revert")}>{t("autonomy.f.revert_hours")}</Label>
          <Input id={id("revert")} type="number" min={0} className="h-8" value={rule.revert_hours} disabled={!editable || rule.level !== "auto_notice"} onChange={(e) => onChange({ revert_hours: e.target.value })} />
        </div>
        <div className="flex items-end gap-1">
          <label className="flex h-8 items-center gap-1 text-sm">
            <input type="checkbox" checked={rule.enabled} disabled={!editable} onChange={(e) => onChange({ enabled: e.target.checked })} /> {t("autonomy.f.enabled")}
          </label>
          {editable ? (
            <>
              <Button variant="ghost" size="icon" aria-label={t("autonomy.move_up")} onClick={() => onMove(-1)}>
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" aria-label={t("autonomy.move_down")} onClick={() => onMove(1)}>
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button variant="ghost" size="icon" aria-label={t("autonomy.remove_rule")} onClick={onRemove}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </>
          ) : null}
        </div>
      </div>
      <div className="mt-2 grid gap-2 md:grid-cols-4">
        <div className="flex flex-col gap-1 md:col-span-2">
          <Label htmlFor={id("partners")}>{t("autonomy.f.partner_ids")}</Label>
          <Input id={id("partners")} className="h-8" value={rule.partner_ids} disabled={!editable} onChange={(e) => onChange({ partner_ids: e.target.value })} placeholder={t("autonomy.any")} />
        </div>
        <div className="flex flex-col gap-1 md:col-span-2">
          <Label htmlFor={id("note")}>{t("autonomy.f.note")}</Label>
          <Input id={id("note")} className="h-8" value={rule.note} disabled={!editable} maxLength={300} onChange={(e) => onChange({ note: e.target.value })} />
        </div>
        {number("amount_max", t("autonomy.f.amount_max"))}
        {number("supplier_score_min", t("autonomy.f.supplier_score_min"), "1")}
        {number("confidence_min", t("autonomy.f.confidence_min"), "0.05")}
        {number("change_pct_max", t("autonomy.f.change_pct_max"))}
        {number("change_days_max", t("autonomy.f.change_days_max"), "1")}
        {number("days_late_max", t("autonomy.f.days_late_max"), "1")}
        <div className="flex flex-col gap-1">
          <Label htmlFor={id("new")}>{t("autonomy.f.first_time_supplier")}</Label>
          <select id={id("new")} className="h-8 rounded-md border bg-card px-2 text-sm" value={rule.first_time_supplier} disabled={!editable} onChange={(e) => onChange({ first_time_supplier: e.target.value as RuleDraft["first_time_supplier"] })}>
            <option value="any">{t("autonomy.any")}</option>
            <option value="yes">{t("autonomy.new_supplier")}</option>
            <option value="no">{t("autonomy.known_supplier")}</option>
          </select>
        </div>
        {rule.kind === "send_email" || rule.kind === "*" ? (
          <fieldset className="flex flex-wrap items-center gap-2 md:col-span-4">
            <legend className="text-sm font-medium">{t("autonomy.f.email_kinds")}</legend>
            {EMAIL_KINDS.map((kind) => (
              <label key={kind} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={rule.email_kinds.includes(kind)}
                  disabled={!editable}
                  onChange={(e) => onChange({ email_kinds: e.target.checked ? [...rule.email_kinds, kind] : rule.email_kinds.filter((k) => k !== kind) })}
                />
                {t(`autonomy.email.${kind}`)}
              </label>
            ))}
          </fieldset>
        ) : null}
      </div>
    </div>
  );
}

function PreviewResult({ result }: { result: PreviewResponse }) {
  const { t, locale } = useI18n();
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm" data-testid="preview">
      <p className="font-medium">{t("autonomy.preview_summary", { alone: result.would_run_alone, total: result.total, days: result.days })}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {Object.entries(result.by_rule).map(([rule, n]) => (
          <Badge key={rule} variant="secondary">
            {rule}: {n}
          </Badge>
        ))}
      </div>
      {result.rows.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("autonomy.col.when")}</TableHead>
              <TableHead>{t("autonomy.col.kind")}</TableHead>
              <TableHead>{t("autonomy.col.summary")}</TableHead>
              <TableHead>{t("autonomy.col.would")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.rows.slice(0, 50).map((row) => (
              <TableRow key={row.approval_id}>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{formatDateTime(row.created_at, locale)}</TableCell>
                <TableCell className="text-xs">{t(`approvals.kind.${row.kind}`)}</TableCell>
                <TableCell className="max-w-[24rem] truncate">{row.summary}</TableCell>
                <TableCell>
                  <Badge variant={row.level === "approve" ? "outline" : "warning"}>
                    {t(`autonomy.level.${row.level}`)}
                    {row.rule_id ? ` · ${row.rule_id}` : ""}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
