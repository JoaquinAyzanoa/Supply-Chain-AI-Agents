import { Link, useNavigate, useSearch } from "@tanstack/react-router";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Empty, ErrorBox, Loading } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import { PageTitle } from "@/routes/placeholders";
import { CASE_KINDS, CASE_STATUSES, DAY_RANGES, useCases, type CaseFilters } from "./api";
import { agentName } from "./labels";

export function CasesPage() {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as CaseFilters;
  const cases = useCases(search);
  const setSearch = (patch: Partial<CaseFilters>) =>
    void navigate({
      to: "/cases",
      search: (prev: CaseFilters) =>
        Object.fromEntries(Object.entries({ ...prev, ...patch }).filter(([, v]) => v)) as CaseFilters,
    });

  return (
    <div>
      <PageTitle title={t("cases.title")}>
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label={t("cases.filter.days")}
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.days ?? "7"}
            onChange={(event) => setSearch({ days: event.target.value })}
          >
            {DAY_RANGES.map((range) => (
              <option key={range} value={range}>
                {t(`cases.days.${range}`)}
              </option>
            ))}
          </select>
          <select
            aria-label={t("cases.filter.status")}
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.status ?? ""}
            onChange={(event) => setSearch({ status: event.target.value })}
          >
            <option value="">{t("cases.filter.all_statuses")}</option>
            {CASE_STATUSES.map((status) => (
              <option key={status} value={status}>
                {t(`cases.status.${status}`)}
              </option>
            ))}
          </select>
          <select
            aria-label={t("cases.filter.kind")}
            className="h-8 rounded-md border bg-card px-2 text-sm"
            value={search.kind ?? ""}
            onChange={(event) => setSearch({ kind: event.target.value })}
          >
            <option value="">{t("cases.filter.all_kinds")}</option>
            {CASE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`cases.kind.${kind}`)}
              </option>
            ))}
          </select>
          <Input
            aria-label={t("cases.filter.po")}
            placeholder={t("cases.filter.po")}
            className="h-8 w-32"
            defaultValue={search.po ?? ""}
            onKeyDown={(event) => {
              if (event.key === "Enter") setSearch({ po: (event.target as HTMLInputElement).value });
            }}
            onBlur={(event) => setSearch({ po: event.target.value })}
          />
        </div>
      </PageTitle>
      {cases.isPending ? <Loading /> : null}
      {cases.error ? <ErrorBox error={cases.error} onRetry={() => cases.refetch()} /> : null}
      {cases.data && cases.data.length === 0 ? <Empty /> : null}
      {cases.data && cases.data.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("cases.col.updated")}</TableHead>
              <TableHead>{t("cases.col.kind")}</TableHead>
              <TableHead>{t("cases.col.case")}</TableHead>
              <TableHead>{t("cases.col.status")}</TableHead>
              <TableHead>{t("cases.col.summary")}</TableHead>
              <TableHead>{t("cases.col.agent")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {cases.data.map((row) => (
              <TableRow key={row.case_id}>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(row.updated_at, locale)}</TableCell>
                <TableCell>
                  <Badge variant="outline">{t(`cases.kind.${row.kind}`)}</Badge>
                </TableCell>
                <TableCell className="font-medium">
                  <Link to="/cases/$caseId" params={{ caseId: row.code }} className="text-primary underline">
                    {row.code}
                  </Link>
                  {row.po_name ? <span className="ml-2 text-muted-foreground">{row.po_name}</span> : null}
                </TableCell>
                <TableCell>
                  <StatusBadge status={row.status} />
                </TableCell>
                <TableCell className="max-w-[28rem] truncate" title={row.summary ?? ""}>
                  {row.summary ?? ""}
                </TableCell>
                <TableCell className="text-muted-foreground">{agentName(t, row.agent)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
