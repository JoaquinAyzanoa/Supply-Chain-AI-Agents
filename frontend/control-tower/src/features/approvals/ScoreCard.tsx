/**
 * The weekly supplier scorecards: one row per supplier with the numbers, the
 * flagged changes and the paragraph the model wrote. Approving writes the
 * scores to Odoo and the observed lead times to the planner.
 */
import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/i18n";
import { formatDate } from "@/lib/utils";
import { SupplierScoreTable, type SupplierScoreRow } from "@/features/suppliers/SupplierScoreTable";
import type { ScoresPayload } from "./types";

export function ScoreCard({ payload }: { payload: ScoresPayload }) {
  const { t, locale } = useI18n();
  const rows: SupplierScoreRow[] = payload.scores;
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">{t("approvals.scores.suppliers", { n: rows.length })}</Badge>
        {payload.period?.start && payload.period?.end ? (
          <span className="text-xs text-muted-foreground">
            {t("approvals.scores.period", { start: formatDate(payload.period.start, locale), end: formatDate(payload.period.end, locale) })}
          </span>
        ) : null}
      </div>
      <SupplierScoreTable rows={rows} />
      <p className="text-xs text-muted-foreground">{t("approvals.scores.writes")}</p>
    </div>
  );
}
