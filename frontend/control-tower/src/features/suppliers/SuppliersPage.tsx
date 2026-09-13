/**
 * Suppliers: the latest scorecard per supplier, from the weekly run the
 * performance agent makes and a person approves.
 */
import { useQuery } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { useI18n } from "@/i18n";
import { formatDate } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { RoundsPanel } from "@/features/sourcing/RoundsPanel";
import { SupplierScoreTable } from "./SupplierScoreTable";

export function useSupplierScores() {
  return useQuery({
    queryKey: ["performance", "scores"],
    queryFn: async () => unwrap(await api.GET("/api/performance/scores")),
  });
}

export function SuppliersPage() {
  const { t, locale } = useI18n();
  const scores = useSupplierScores();
  const latest = scores.data?.[0];
  return (
    <div>
      <PageTitle title={t("suppliers.title")}>
        {latest ? (
          <span className="text-xs text-muted-foreground">
            {t("suppliers.period", { start: formatDate(latest.period_start, locale), end: formatDate(latest.period_end, locale) })}
          </span>
        ) : null}
      </PageTitle>
      <div className="p-4">
        <p className="mb-3 text-sm text-muted-foreground">{t("suppliers.intro")}</p>
        {scores.isPending ? <Loading /> : null}
        {scores.error ? <ErrorBox error={scores.error} onRetry={() => scores.refetch()} /> : null}
        {scores.data && scores.data.length === 0 ? <Empty text={t("suppliers.empty")} /> : null}
        {scores.data && scores.data.length ? <SupplierScoreTable rows={scores.data} withProfile /> : null}
        <div className="mt-6">
          <RoundsPanel />
        </div>
      </div>
    </div>
  );
}
