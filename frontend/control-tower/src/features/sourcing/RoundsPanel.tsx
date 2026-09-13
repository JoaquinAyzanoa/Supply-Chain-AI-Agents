/**
 * Quote rounds: every round the sourcing agent ran, who was invited, who
 * answered, where the award stands. An approver can start a round on an order
 * and compare the quotes before the deadline.
 */
import { Link } from "@tanstack/react-router";
import { Gavel, Play, Scale } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/auth/AuthProvider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input, Label } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { useCompareRound, useRounds, useStartRound, type SourcingRound } from "./api";

export function RoundsPanel({ partnerId }: { partnerId?: number | null }) {
  const { t } = useI18n();
  const { hasRole } = useAuth();
  const rounds = useRounds(partnerId);
  const [message, setMessage] = useState<string | null>(null);
  const canAct = hasRole("approver");
  return (
    <section className="space-y-3" aria-label={t("sourcing.title")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{t("sourcing.title")}</h2>
      </div>
      <p className="text-sm text-muted-foreground">{t("sourcing.intro")}</p>
      {canAct ? <StartRound onDone={setMessage} /> : null}
      {message ? (
        <p className="text-sm text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
      {rounds.isPending ? <Loading /> : null}
      {rounds.error ? <ErrorBox error={rounds.error} onRetry={() => rounds.refetch()} /> : null}
      {rounds.data && rounds.data.length === 0 ? <Empty text={t("sourcing.empty")} /> : null}
      {rounds.data && rounds.data.length ? <RoundsTable rows={rounds.data} canAct={canAct} onDone={setMessage} /> : null}
    </section>
  );
}

function StartRound({ onDone }: { onDone: (message: string) => void }) {
  const { t } = useI18n();
  const start = useStartRound();
  const [poName, setPoName] = useState("");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const name = poName.trim().toUpperCase();
    if (!name) return;
    try {
      const result = await start.mutateAsync({ po_name: name });
      onDone(result.summary);
      setPoName("");
    } catch (error) {
      onDone(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-wrap items-end gap-2 rounded-md border bg-card p-3" aria-label={t("sourcing.start.title")}>
      <div>
        <Label htmlFor="round-po">{t("sourcing.start.po")}</Label>
        <Input id="round-po" value={poName} onChange={(event) => setPoName(event.target.value)} placeholder="P00081" className="w-32" />
      </div>
      <Button type="submit" size="sm" disabled={start.isPending || !poName.trim()}>
        <Play className="h-4 w-4" /> {t("sourcing.start.button")}
      </Button>
      <p className="basis-full text-xs text-muted-foreground">{t("sourcing.start.hint")}</p>
    </form>
  );
}

function RoundsTable({ rows, canAct, onDone }: { rows: SourcingRound[]; canAct: boolean; onDone: (message: string) => void }) {
  const { t, locale } = useI18n();
  const compare = useCompareRound();
  const run = async (round: SourcingRound) => {
    try {
      const result = await compare.mutateAsync(round.id);
      onDone(result.summary);
    } catch (error) {
      onDone(error instanceof Error ? error.message : String(error));
    }
  };
  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("sourcing.col.round")}</TableHead>
            <TableHead>{t("sourcing.col.basket")}</TableHead>
            <TableHead>{t("sourcing.col.invited")}</TableHead>
            <TableHead>{t("sourcing.col.status")}</TableHead>
            <TableHead>{t("sourcing.col.deadline")}</TableHead>
            {canAct ? <TableHead /> : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((round) => (
            <TableRow key={round.id}>
              <TableCell className="font-medium">
                #{round.id}
                {round.source_po_name ? (
                  <>
                    {" · "}
                    <Link to="/" search={{ po: round.source_po_name }} className="text-primary underline">
                      {round.source_po_name}
                    </Link>
                  </>
                ) : null}
              </TableCell>
              <TableCell>{round.basket.map((line) => `${line.product} × ${line.qty}`).join(" · ")}</TableCell>
              <TableCell>
                <ul className="space-y-0.5 text-xs">
                  {round.rfqs.map((rfq) => (
                    <li key={rfq.partner_id}>
                      {rfq.partner_name}
                      {rfq.po_name ? ` · ${rfq.po_name}` : ""} · {t(`sourcing.rfq.${rfq.status}`)}
                      {rfq.replied_at ? ` · ${t("sourcing.replied", { at: formatDateTime(rfq.replied_at, locale) })}` : ""}
                    </li>
                  ))}
                </ul>
              </TableCell>
              <TableCell>
                <Badge variant={round.status === "awarded" ? "success" : round.status === "awaiting_award" ? "warning" : round.status === "open" ? "secondary" : "outline"}>
                  {t(`sourcing.status.${round.status}`)}
                </Badge>
                {round.awarded_po_name ? <div className="text-xs text-muted-foreground">{round.awarded_po_name}</div> : null}
                {round.award_approval_id && round.status === "awaiting_award" ? (
                  <Link to="/approvals" search={{ id: round.award_approval_id }} className="block text-xs text-primary underline">
                    <Gavel className="mr-1 inline h-3 w-3" />
                    {t("sourcing.open_award", { id: round.award_approval_id })}
                  </Link>
                ) : null}
              </TableCell>
              <TableCell className="text-muted-foreground">{formatDateTime(round.deadline, locale)}</TableCell>
              {canAct ? (
                <TableCell className="text-right">
                  {round.status === "open" || round.status === "comparing" || round.status === "rejected" ? (
                    <Button variant="outline" size="sm" onClick={() => void run(round)} disabled={compare.isPending} aria-label={t("sourcing.compare_round", { id: round.id })}>
                      <Scale className="h-4 w-4" /> {t("sourcing.compare")}
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
