/**
 * The demand calendar: promotions, holidays and projects people know about. The
 * planner normalises the history for past events and raises the forecast for the
 * ones ahead. Approvers add and remove entries; everyone reads them.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate } from "@/lib/utils";

export interface CalendarEvent {
  id?: number | null;
  kind: "promotion" | "holiday" | "project";
  name: string;
  start_date: string;
  end_date: string;
  product_id?: number | null;
  category?: string | null;
  factor: number;
  quantity: number;
  note: string;
  created_by?: string | null;
  created_at?: string | null;
}

export function useCalendar() {
  return useQuery({
    queryKey: ["planning", "calendar"],
    queryFn: async () => unwrap(await api.GET("/api/planning/calendar")) as unknown as CalendarEvent[],
  });
}

const EMPTY: CalendarEvent = { kind: "promotion", name: "", start_date: "", end_date: "", product_id: null, category: null, factor: 1.5, quantity: 0, note: "" };

export function DemandCalendar() {
  const { t, locale } = useI18n();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const events = useCalendar();
  const [draft, setDraft] = useState<CalendarEvent>(EMPTY);
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");
  const add = useMutation({
    mutationFn: async (event: CalendarEvent) => unwrap(await api.POST("/api/planning/calendar", { body: event as never })) as unknown as CalendarEvent,
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ["planning", "calendar"] });
      setMessage(t("calendar.added", { name: saved.name }));
      setDraft(EMPTY);
    },
    onError: (error) => setMessage(error instanceof Error ? error.message : String(error)),
  });
  const remove = useMutation({
    // a 204 carries no body, so the usual unwrap (which wants data) does not apply
    mutationFn: async (id: number) => {
      const result = await api.DELETE("/api/planning/calendar/{event_id}", { params: { path: { event_id: id } } });
      if (!result.response.ok) unwrap(result);
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["planning", "calendar"] }),
  });
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setMessage(null);
    add.mutate({
      ...draft,
      product_id: draft.product_id || null,
      category: draft.category || null,
      factor: draft.kind === "project" ? 1 : Number(draft.factor),
      quantity: draft.kind === "project" ? Number(draft.quantity) : 0,
    });
  };
  return (
    <section className="space-y-3" aria-label={t("calendar.title")}>
      <h2 className="text-base font-semibold">{t("calendar.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("calendar.intro")}</p>
      {canAct ? (
        <form onSubmit={submit} className="grid gap-2 rounded-md border bg-card p-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={t("calendar.add")}>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-kind">{t("calendar.f.kind")}</Label>
            <select id="cal-kind" className="h-9 rounded-md border bg-background px-2 text-sm" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as CalendarEvent["kind"], factor: e.target.value === "holiday" ? 0.5 : 1.5 })}>
              <option value="promotion">{t("calendar.kind.promotion")}</option>
              <option value="holiday">{t("calendar.kind.holiday")}</option>
              <option value="project">{t("calendar.kind.project")}</option>
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-name">{t("calendar.f.name")}</Label>
            <Input id="cal-name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-start">{t("calendar.f.start")}</Label>
            <Input id="cal-start" type="date" value={draft.start_date} onChange={(e) => setDraft({ ...draft, start_date: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-end">{t("calendar.f.end")}</Label>
            <Input id="cal-end" type="date" value={draft.end_date} onChange={(e) => setDraft({ ...draft, end_date: e.target.value })} required />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-product">{t("calendar.f.product")}</Label>
            <Input id="cal-product" type="number" value={draft.product_id ?? ""} onChange={(e) => setDraft({ ...draft, product_id: e.target.value ? Number(e.target.value) : null })} placeholder={t("calendar.f.product_hint")} required={draft.kind === "project"} />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="cal-category">{t("calendar.f.category")}</Label>
            <Input id="cal-category" value={draft.category ?? ""} onChange={(e) => setDraft({ ...draft, category: e.target.value || null })} placeholder={t("calendar.f.category_hint")} />
          </div>
          {draft.kind === "project" ? (
            <div className="flex flex-col gap-1">
              <Label htmlFor="cal-qty">{t("calendar.f.quantity")}</Label>
              <Input id="cal-qty" type="number" step="1" min={1} value={draft.quantity || ""} onChange={(e) => setDraft({ ...draft, quantity: Number(e.target.value) })} required />
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <Label htmlFor="cal-factor">{t("calendar.f.factor")}</Label>
              <Input id="cal-factor" type="number" step="0.1" min={0.1} max={10} value={draft.factor} onChange={(e) => setDraft({ ...draft, factor: Number(e.target.value) })} />
              <span className="text-xs text-muted-foreground">{t("calendar.f.factor_hint")}</span>
            </div>
          )}
          <div className="flex items-end">
            <Button type="submit" size="sm" disabled={add.isPending}>
              <CalendarPlus className="h-4 w-4" /> {t("calendar.add")}
            </Button>
          </div>
        </form>
      ) : null}
      {message ? (
        <p className="text-sm text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
      {events.isPending ? <Loading /> : null}
      {events.error ? <ErrorBox error={events.error} onRetry={() => events.refetch()} /> : null}
      {events.data && events.data.length === 0 ? <Empty text={t("calendar.empty")} /> : null}
      {events.data && events.data.length ? (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("calendar.col.event")}</TableHead>
                <TableHead>{t("calendar.col.when")}</TableHead>
                <TableHead>{t("calendar.col.scope")}</TableHead>
                <TableHead className="text-right">{t("calendar.col.effect")}</TableHead>
                {canAct ? <TableHead /> : null}
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.data.map((e) => (
                <TableRow key={e.id ?? e.name}>
                  <TableCell>
                    <Badge variant="outline" className="mr-2">
                      {t(`calendar.kind.${e.kind}`)}
                    </Badge>
                    {e.name}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDate(e.start_date, locale)} → {formatDate(e.end_date, locale)}
                  </TableCell>
                  <TableCell>{e.product_id ? t("calendar.scope.product", { id: e.product_id }) : e.category ? t("calendar.scope.category", { name: e.category }) : t("calendar.scope.all")}</TableCell>
                  <TableCell className="text-right tabular-nums">{e.kind === "project" ? t("calendar.effect.units", { n: e.quantity }) : t("calendar.effect.factor", { factor: e.factor })}</TableCell>
                  {canAct ? (
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" aria-label={t("calendar.remove", { name: e.name })} onClick={() => e.id && remove.mutate(e.id)} disabled={remove.isPending}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  ) : null}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
    </section>
  );
}
