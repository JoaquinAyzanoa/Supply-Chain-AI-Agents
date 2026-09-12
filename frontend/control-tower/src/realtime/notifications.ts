/**
 * The bell: what happened while the person was not looking. Server events
 * carry ids only, so each one is turned into a line by fetching the record
 * it names (an approval, a case, a run). Kept in memory for the session,
 * newest first, at most fifty; the stream hook feeds it.
 */
import { useSyncExternalStore } from "react";

import { api, unwrap } from "@/api/client";

export type NotificationKind = "approval" | "escalation" | "run_failed" | "approval_resolved";

export interface Notification {
  id: string;
  kind: NotificationKind;
  at: string;
  text: string;
  /** Where clicking takes the person. */
  to: { path: "/approvals"; id?: number } | { path: "/cases/$caseId"; caseId: string } | { path: "/runs" };
  read: boolean;
}

const MAX = 50;
let items: Notification[] = [];
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export const notifications = {
  push(item: Omit<Notification, "read" | "at"> & { at?: string }): void {
    if (items.some((existing) => existing.id === item.id)) return;
    items = [{ ...item, at: item.at ?? new Date().toISOString(), read: false }, ...items].slice(0, MAX);
    emit();
  },
  markAllRead(): void {
    if (!items.some((item) => !item.read)) return;
    items = items.map((item) => ({ ...item, read: true }));
    emit();
  },
  clear(): void {
    items = [];
    emit();
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  get(): Notification[] {
    return items;
  },
};

export function useNotifications(): Notification[] {
  return useSyncExternalStore(notifications.subscribe, notifications.get, notifications.get);
}

type StreamData = { case_id?: string | null; approval_id?: number | null; run_id?: string | null; status?: string | null; po_name?: string | null };

/**
 * Turn one server event into a notification, or nothing when it is routine
 * (a case event that needs no one, an approval resolved by someone else is
 * still worth a line so the inbox count makes sense).
 */
export async function notifyFor(event: string, raw: string): Promise<void> {
  let data: StreamData = {};
  try {
    data = raw ? (JSON.parse(raw) as StreamData) : {};
  } catch {
    return;
  }
  try {
    if (event === "approval_created" && typeof data.approval_id === "number") {
      const approval = unwrap(await api.GET("/api/approvals/{approval_id}", { params: { path: { approval_id: data.approval_id } } }));
      notifications.push({
        id: `approval:${approval.id}`,
        kind: "approval",
        text: approval.summary,
        to: { path: "/approvals", id: approval.id },
      });
    } else if (event === "approval_resolved" && typeof data.approval_id === "number") {
      const approval = unwrap(await api.GET("/api/approvals/{approval_id}", { params: { path: { approval_id: data.approval_id } } }));
      notifications.push({
        id: `resolved:${approval.id}:${approval.status}`,
        kind: "approval_resolved",
        text: `${approval.summary} · ${approval.status}${approval.resolved_by ? ` · ${approval.resolved_by}` : ""}`,
        to: { path: "/approvals", id: approval.id },
      });
    } else if (event === "case_updated" && data.status === "escalated" && data.case_id) {
      const detail = unwrap(await api.GET("/api/cases/{case_id}", { params: { path: { case_id: data.case_id } } }));
      notifications.push({
        id: `escalated:${detail.case.case_id}:${detail.case.updated_at}`,
        kind: "escalation",
        text: [detail.case.code, detail.case.po_name, detail.case.summary].filter(Boolean).join(" · "),
        to: { path: "/cases/$caseId", caseId: detail.case.code },
      });
    } else if (event === "run_finished" && data.status === "failed") {
      notifications.push({
        id: `run:${data.run_id ?? data.case_id ?? Date.now()}`,
        kind: "run_failed",
        text: data.case_id ?? data.run_id ?? "",
        to: { path: "/runs" },
      });
    }
  } catch {
    /* the record is gone or the session expired: no line is better than a broken one */
  }
}
