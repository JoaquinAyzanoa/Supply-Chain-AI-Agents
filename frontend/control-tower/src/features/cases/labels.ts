/**
 * Human names for the identifiers the director records on cases: event types,
 * task kinds, agents, sources and approval kinds. Unknown values fall back to
 * the identifier with underscores and dots spaced out, never to nothing.
 */
import type { MessageKey } from "@/i18n";

type T = (key: MessageKey | string, values?: Record<string, string | number>) => string;

export function labelFor(t: T, prefix: "event" | "task" | "agent" | "source" | "approval", value: string): string {
  const key = `${prefix}.${value}`;
  const label = t(key);
  return label === key ? value.replace(/[._]/g, " ") : label;
}

/** ``supplier_comms`` -> "Supplier agent"; unknown agents keep their identifier spaced out. */
export function agentName(t: T, agent: string | null | undefined): string {
  if (!agent) return "";
  const key = `agent.name.${agent}`;
  const label = t(key);
  return label === key ? agent.replace(/[._]/g, " ") : label;
}

/** The agent's failure document, when a result payload's error or summary carries one. */
export function failureOf(payload: Record<string, unknown>): { message: string; details: string | null } | null {
  const error = payload.error;
  if (error && typeof error === "object") {
    const doc = error as Record<string, unknown>;
    const message = typeof doc.message === "string" ? doc.message : typeof doc.code === "string" ? doc.code : "failed";
    return { message, details: JSON.stringify(doc, null, 2) };
  }
  const summary = typeof payload.summary === "string" ? payload.summary : "";
  if (summary.startsWith("{")) {
    try {
      const doc = JSON.parse(summary) as Record<string, unknown>;
      return { message: String(doc.message ?? doc.code ?? "failed"), details: JSON.stringify(doc, null, 2) };
    } catch {
      /* not JSON after all */
    }
  }
  return null;
}

/** The API sends ``code`` (C00012); an id alone is shortened for the rare row without one. */
export function shortCaseId(caseId: string, code?: string | null): string {
  return code ?? `#${caseId.replace(/^case_/, "").slice(0, 8)}`;
}
