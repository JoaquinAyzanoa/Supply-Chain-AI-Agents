/**
 * A bell with the unread count and a small list of what happened: new
 * approvals, escalations, failed runs. Opening the list marks everything read.
 */
import { useNavigate } from "@tanstack/react-router";
import { Bell } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n";
import { cn, formatDateTime } from "@/lib/utils";
import { notifications, useNotifications, type Notification } from "@/realtime/notifications";

export function NotificationsBell({ compact = false }: { compact?: boolean }) {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const items = useNotifications();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const unread = items.filter((item) => !item.read).length;

  useEffect(() => {
    if (!open) return;
    notifications.markAllRead();
    const onClick = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const go = (item: Notification) => {
    setOpen(false);
    if (item.to.path === "/approvals") void navigate({ to: "/approvals", search: item.to.id !== undefined ? { id: item.to.id } : {} });
    else if (item.to.path === "/cases/$caseId") void navigate({ to: "/cases/$caseId", params: { caseId: item.to.caseId } });
    else void navigate({ to: "/runs", search: {} });
  };

  return (
    <div ref={root} className="relative">
      <Button
        variant="ghost"
        size={compact ? "icon" : "sm"}
        className={compact ? "" : "justify-start px-0"}
        aria-label={unread ? t("notifications.unread", { n: unread }) : t("notifications.title")}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="relative">
          <Bell className="h-4 w-4" />
          {unread ? (
            <span className="absolute -right-2 -top-2 min-w-4 rounded-full bg-destructive px-1 text-center text-[10px] font-semibold leading-4 text-destructive-foreground">
              {unread}
            </span>
          ) : null}
        </span>
        {compact ? null : <span>{t("notifications.title")}</span>}
      </Button>
      {open ? (
        <div
          role="region"
          aria-label={t("notifications.title")}
          className={cn(
            "absolute z-50 w-80 rounded-md border bg-card p-2 shadow-lg",
            compact ? "right-0 top-full mt-1" : "bottom-full left-0 mb-1",
          )}
        >
          <div className="mb-1 flex items-center justify-between px-1">
            <span className="text-xs font-semibold">{t("notifications.title")}</span>
            {items.length ? (
              <button type="button" className="text-xs text-muted-foreground underline" onClick={() => notifications.clear()}>
                {t("notifications.clear")}
              </button>
            ) : null}
          </div>
          {items.length === 0 ? <p className="px-1 py-2 text-xs text-muted-foreground">{t("notifications.empty")}</p> : null}
          <ul className="max-h-80 overflow-y-auto">
            {items.map((item) => (
              <li key={item.id}>
                <button type="button" className="w-full rounded px-1 py-1.5 text-left text-sm hover:bg-accent" onClick={() => go(item)}>
                  <span className="block text-xs text-muted-foreground">
                    {t(`notifications.kind.${item.kind}`)} · {formatDateTime(item.at, locale)}
                  </span>
                  <span className="block truncate" title={item.text}>
                    {item.text}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
