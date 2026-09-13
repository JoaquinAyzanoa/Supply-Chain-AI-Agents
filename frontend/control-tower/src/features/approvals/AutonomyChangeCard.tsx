/**
 * An autonomy change waiting for a second person: who asked, which rules widen
 * autonomy, and the whole policy that would apply on approval.
 */
import { Link } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import type { AutonomyChangePayload } from "./types";

function describe(when: Record<string, unknown>, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const parts: string[] = [];
  const list = (v: unknown) => (Array.isArray(v) && v.length ? v.join(", ") : null);
  const num = (v: unknown) => (typeof v === "number" ? v : null);
  const partners = list(when.partner_ids);
  const kinds = list(when.email_kinds);
  if (partners) parts.push(t("autonomy.d.partners", { list: partners }));
  if (kinds) parts.push(t("autonomy.d.email_kinds", { list: kinds }));
  const amount = num(when.amount_max);
  if (amount !== null) parts.push(t("autonomy.d.amount_max", { n: amount }));
  const score = num(when.supplier_score_min);
  if (score !== null) parts.push(t("autonomy.d.score_min", { n: score }));
  const conf = num(when.confidence_min);
  if (conf !== null) parts.push(t("autonomy.d.confidence_min", { n: conf }));
  const pct = num(when.change_pct_max);
  if (pct !== null) parts.push(t("autonomy.d.change_pct_max", { n: pct }));
  const days = num(when.change_days_max);
  if (days !== null) parts.push(t("autonomy.d.change_days_max", { n: days }));
  const late = num(when.days_late_max);
  if (late !== null) parts.push(t("autonomy.d.days_late_max", { n: late }));
  if (when.first_time_supplier === true) parts.push(t("autonomy.new_supplier"));
  if (when.first_time_supplier === false) parts.push(t("autonomy.known_supplier"));
  return parts.join(" · ") || t("autonomy.d.always");
}

export function AutonomyChangeCard({ payload }: { payload: AutonomyChangePayload }) {
  const { t } = useI18n();
  const widened = new Set(payload.widened);
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p>{t("approvals.autonomy.intro", { by: payload.requested_by_name || payload.requested_by || "-" })}</p>
      {payload.note ? <p className="text-muted-foreground">{payload.note}</p> : null}
      <div className="flex flex-wrap gap-1">
        {payload.widened.map((id) => (
          <Badge key={id} variant="warning">
            {t("approvals.autonomy.widened", { id })}
          </Badge>
        ))}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("autonomy.f.id")}</TableHead>
            <TableHead>{t("autonomy.f.kind")}</TableHead>
            <TableHead>{t("autonomy.f.conditions")}</TableHead>
            <TableHead>{t("autonomy.f.level")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {payload.policy.rules.map((rule) => (
            <TableRow key={rule.id} className={widened.has(rule.id) ? "bg-warning/10" : ""}>
              <TableCell className="font-medium">{rule.id}</TableCell>
              <TableCell>{rule.kind === "*" ? t("autonomy.any_kind") : t(`approvals.kind.${rule.kind}`)}</TableCell>
              <TableCell className="text-xs text-muted-foreground">{describe(rule.when, t)}</TableCell>
              <TableCell>
                <Badge variant={rule.level === "approve" ? "outline" : "warning"}>{t(`autonomy.level.${rule.level}`)}</Badge>
                {!rule.enabled ? ` ${t("approvals.autonomy.disabled")}` : ""}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="text-xs text-muted-foreground">{t("approvals.autonomy.rule")}</p>
      <Link to="/autonomy" className="inline-flex items-center gap-1 text-primary underline">
        {t("approvals.autonomy.open")} <ExternalLink className="h-3 w-3" />
      </Link>
    </div>
  );
}
