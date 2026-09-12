import { Link } from "@tanstack/react-router";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDate, formatDateTime, formatNumber } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { usePlanningRuns } from "./api";

export function PlanningPage() {
  const { t, locale } = useI18n();
  const runs = usePlanningRuns();
  return (
    <div>
      <PageTitle title={t("planning.title")} />
      {runs.isPending ? <Loading /> : null}
      {runs.error ? <ErrorBox error={runs.error} onRetry={() => runs.refetch()} /> : null}
      {runs.data && runs.data.length === 0 ? <Empty text={t("planning.empty")} /> : null}
      {runs.data && runs.data.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("planning.col.as_of")}</TableHead>
              <TableHead>{t("planning.col.kind")}</TableHead>
              <TableHead>{t("planning.col.status")}</TableHead>
              <TableHead className="text-right">{t("planning.col.lines")}</TableHead>
              <TableHead className="text-right">{t("planning.col.rfqs")}</TableHead>
              <TableHead className="text-right">{t("planning.col.exceptions")}</TableHead>
              <TableHead>{t("planning.col.summary")}</TableHead>
              <TableHead>{t("planning.col.created")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.map((run) => (
              <TableRow key={run.run_id}>
                <TableCell className="whitespace-nowrap font-medium">
                  <Link to="/planning/$runId" params={{ runId: run.run_id }} className="text-primary underline">
                    {formatDate(run.as_of, locale)}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{t(`planning.kind.${run.kind}`)}</Badge>
                </TableCell>
                <TableCell>
                  <StatusBadge status={run.status} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatNumber(run.totals.lines, locale, 0)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatNumber(run.totals.rfq_lines, locale, 0)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatNumber(run.totals.exceptions, locale, 0)}</TableCell>
                <TableCell className="max-w-[28rem] truncate" title={run.summary ?? ""}>
                  {run.summary ?? ""}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(run.created_at, locale)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
