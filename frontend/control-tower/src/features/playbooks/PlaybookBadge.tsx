/**
 * Where an order is in its playbook, in one line: the plan, the step and when
 * the next move is due. Used on board cards, in the drawer and on approvals.
 */
import { ListChecks } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/i18n";
import { formatDateTime } from "@/lib/utils";
import type { PlaybookPosition } from "./api";

export function PlaybookBadge({ position, compact = false }: { position: PlaybookPosition; compact?: boolean }) {
  const { t, locale } = useI18n();
  const step = t("playbooks.step_of", { n: position.step_index + 1, total: position.steps_total });
  const title = position.step_label ? `${position.title} · ${step} · ${position.step_label}` : `${position.title} · ${step}`;
  return (
    <Badge variant="secondary" className="max-w-full gap-1 font-normal" title={title} data-testid="playbook-badge">
      <ListChecks className="h-3 w-3 shrink-0" />
      <span className="truncate">
        {position.title}
        {compact ? ` · ${step}` : position.step_label ? ` · ${position.step_label}` : ""}
        {position.due_at && !compact ? ` · ${t("playbooks.due", { at: formatDateTime(position.due_at, locale) })}` : ""}
      </span>
    </Badge>
  );
}

/** The plan ahead: the coming steps and what happens if a person says no. */
export function PlaybookOutlook({ position }: { position: PlaybookPosition }) {
  const { t, locale } = useI18n();
  return (
    <div className="space-y-1 text-sm" data-testid="playbook-outlook">
      <div className="flex flex-wrap items-center gap-2">
        <PlaybookBadge position={position} />
        <span className="text-xs text-muted-foreground">{t(`playbooks.status.${position.status}`)}</span>
      </div>
      {position.due_at ? <p className="text-xs text-muted-foreground">{t("playbooks.due", { at: formatDateTime(position.due_at, locale) })}</p> : null}
      {position.next_steps.length ? (
        <div>
          <div className="text-xs font-medium uppercase text-muted-foreground">{t("playbooks.next_steps")}</div>
          <ol className="list-decimal pl-5 text-sm">
            {position.next_steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">{t("playbooks.last_step")}</p>
      )}
      {position.if_rejected ? (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium">{t("playbooks.if_rejected")}: </span>
          {position.if_rejected}
        </p>
      ) : null}
    </div>
  );
}
