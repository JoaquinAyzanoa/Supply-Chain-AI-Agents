/** Screens that later stories fill in; the shell, routing and auth are real already. */
import { useT, type MessageKey } from "@/i18n";

export function PageTitle({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-card px-4 py-3">
      <h1 className="text-lg font-semibold">{title}</h1>
      {children}
    </div>
  );
}

function Soon({ titleKey }: { titleKey: MessageKey }) {
  const t = useT();
  return (
    <div>
      <PageTitle title={t(titleKey)} />
      <p className="p-4 text-sm text-muted-foreground">{t("page.soon")}</p>
    </div>
  );
}

export const ApprovalsPage = () => <Soon titleKey="approvals.title" />;
export const CasesPage = () => <Soon titleKey="cases.title" />;
export const CaseDetailPage = () => <Soon titleKey="cases.title" />;
export const ExceptionsPage = () => <Soon titleKey="exceptions.title" />;
export const PlanningPage = () => <Soon titleKey="planning.title" />;
export const PlanningRunPage = () => <Soon titleKey="planning.title" />;
export const RunsPage = () => <Soon titleKey="runs.title" />;
export const SettingsPage = () => <Soon titleKey="settings.title" />;
