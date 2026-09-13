/**
 * How to write to this supplier: tone, greeting, sign-off, contacts and notes the
 * buyers keep; the agents read it when they draft. Facts the agents keep are shown
 * read-only.
 */
import { Save } from "lucide-react";
import { useEffect, useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { useSaveProfile, useSupplierProfile, type ProfileUpdate } from "@/features/learning/api";
import { useI18n } from "@/i18n";

const FORMALITY = ["", "formal", "neutral", "informal"] as const;
/** The two facts people set by hand: the planner reads them to consolidate orders. */
const FREIGHT_FACTS = new Set(["free_freight_over", "freight_cost"]);

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function SupplierProfileEditor({ partnerId }: { partnerId: number }) {
  const { t } = useI18n();
  const { hasRole } = useAuth();
  const profile = useSupplierProfile(partnerId);
  const save = useSaveProfile(partnerId);
  const [draft, setDraft] = useState<ProfileUpdate | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (profile.data)
      setDraft({
        language: profile.data.language ?? null,
        formality: profile.data.formality ?? null,
        greeting: profile.data.greeting ?? null,
        sign_off: profile.data.sign_off ?? null,
        contacts: [...(profile.data.contacts ?? [])],
        notes: profile.data.notes ?? "",
        free_freight_over: numberOrNull(profile.data.facts?.free_freight_over),
        freight_cost: numberOrNull(profile.data.facts?.freight_cost),
      });
  }, [profile.data]);
  if (profile.isPending || !draft) return <Loading />;
  if (profile.error) return <ErrorBox error={profile.error} onRetry={() => profile.refetch()} />;
  const editable = hasRole("approver");
  const id = (field: string) => `profile-${partnerId}-${field}`;
  const submit = async () => {
    setMessage(null);
    try {
      await save.mutateAsync(draft);
      setMessage(t("suppliers.profile.saved"));
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };
  const facts = Object.entries(profile.data.facts ?? {}).filter(([k]) => !FREIGHT_FACTS.has(k));
  const setNumber = (field: "free_freight_over" | "freight_cost", raw: string) => setDraft({ ...draft, [field]: raw === "" ? null : Math.max(0, Number(raw)) });
  return (
    <div className="grid gap-2 md:grid-cols-4" data-testid={`profile-${partnerId}`}>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("formality")}>{t("suppliers.profile.formality")}</Label>
        <select id={id("formality")} className="h-8 rounded-md border bg-card px-2 text-sm" value={draft.formality ?? ""} disabled={!editable} onChange={(e) => setDraft({ ...draft, formality: e.target.value || null })}>
          {FORMALITY.map((f) => (
            <option key={f} value={f}>
              {f ? t(`suppliers.profile.f.${f}`) : t("autonomy.any")}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("greeting")}>{t("suppliers.profile.greeting")}</Label>
        <Input id={id("greeting")} className="h-8" value={draft.greeting ?? ""} disabled={!editable} onChange={(e) => setDraft({ ...draft, greeting: e.target.value || null })} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("sign_off")}>{t("suppliers.profile.sign_off")}</Label>
        <Input id={id("sign_off")} className="h-8" value={draft.sign_off ?? ""} disabled={!editable} onChange={(e) => setDraft({ ...draft, sign_off: e.target.value || null })} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("contacts")}>{t("suppliers.profile.contacts")}</Label>
        <Input id={id("contacts")} className="h-8" value={draft.contacts.join(", ")} disabled={!editable} onChange={(e) => setDraft({ ...draft, contacts: e.target.value.split(",").map((c) => c.trim()).filter(Boolean) })} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("free_freight_over")}>{t("suppliers.profile.free_freight_over")}</Label>
        <Input id={id("free_freight_over")} className="h-8" type="number" min={0} step="1" value={draft.free_freight_over ?? ""} disabled={!editable} onChange={(e) => setNumber("free_freight_over", e.target.value)} />
      </div>
      <div className="flex flex-col gap-1">
        <Label htmlFor={id("freight_cost")}>{t("suppliers.profile.freight_cost")}</Label>
        <Input id={id("freight_cost")} className="h-8" type="number" min={0} step="1" value={draft.freight_cost ?? ""} disabled={!editable} onChange={(e) => setNumber("freight_cost", e.target.value)} />
      </div>
      <div className="flex flex-col gap-1 md:col-span-2">
        <span className="text-xs text-muted-foreground">{t("suppliers.profile.freight_hint")}</span>
      </div>
      <div className="flex flex-col gap-1 md:col-span-4">
        <Label htmlFor={id("notes")}>{t("suppliers.profile.notes")}</Label>
        <textarea id={id("notes")} className="min-h-16 rounded-md border bg-card p-2 text-sm" value={draft.notes ?? ""} disabled={!editable} maxLength={2000} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
      </div>
      {facts.length ? (
        <p className="text-xs text-muted-foreground md:col-span-3">
          {t("suppliers.profile.facts")}: {facts.map(([k, v]) => `${k} ${String(v)}`).join(" · ")}
        </p>
      ) : (
        <span className="md:col-span-3" />
      )}
      {editable ? (
        <div className="flex items-center justify-end gap-2">
          {message ? (
            <span className="text-xs text-muted-foreground" role="status">
              {message}
            </span>
          ) : null}
          <Button size="sm" onClick={submit} disabled={save.isPending}>
            <Save className="h-4 w-4" /> {t("suppliers.profile.save")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
