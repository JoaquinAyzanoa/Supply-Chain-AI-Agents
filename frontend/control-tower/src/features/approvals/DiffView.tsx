/**
 * What an approver changed in a draft before approving it: the original and the edited
 * text side by side as a line diff (removed lines in red, added lines in green). HTML
 * becomes plain lines first, so the diff reads like the email will.
 */
import { useI18n } from "@/i18n";

export function htmlToLines(html: string): string[] {
  const text = html
    .replace(/<\s*(br|\/p|\/div|\/li|\/tr|\/h[1-6])\s*>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export type DiffLine = { kind: "same" | "removed" | "added"; text: string };

/** A longest-common-subsequence line diff; fine for the size of an email. */
export function diffLines(before: string[], after: string[]): DiffLine[] {
  const n = before.length;
  const m = after.length;
  const table: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i]![j] = before[i] === after[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) {
      out.push({ kind: "same", text: before[i]! });
      i++;
      j++;
    } else if (table[i + 1]![j]! >= table[i]![j + 1]!) {
      out.push({ kind: "removed", text: before[i]! });
      i++;
    } else {
      out.push({ kind: "added", text: after[j]! });
      j++;
    }
  }
  while (i < n) out.push({ kind: "removed", text: before[i++]! });
  while (j < m) out.push({ kind: "added", text: after[j++]! });
  return out;
}

export function DiffView({ before, after, subjectBefore, subjectAfter }: { before: string; after: string; subjectBefore: string; subjectAfter: string }) {
  const { t } = useI18n();
  const lines = diffLines(htmlToLines(before), htmlToLines(after));
  const changed = lines.some((l) => l.kind !== "same") || subjectBefore !== subjectAfter;
  return (
    <div className="rounded-md border bg-muted/40 p-2 text-xs" data-testid="diff-view" aria-label={t("approvals.diff.title")}>
      <div className="mb-1 font-medium uppercase text-muted-foreground">{t("approvals.diff.title")}</div>
      {!changed ? <p className="text-muted-foreground">{t("approvals.diff.none")}</p> : null}
      {subjectBefore !== subjectAfter ? (
        <div className="mb-1">
          <div className="text-destructive line-through">{subjectBefore}</div>
          <div className="text-success-text">{subjectAfter}</div>
        </div>
      ) : null}
      {changed ? (
        <pre className="whitespace-pre-wrap font-sans">
          {lines.map((line, index) => (
            <div key={index} className={line.kind === "removed" ? "bg-destructive/10 text-destructive line-through" : line.kind === "added" ? "bg-success/10 text-success-text" : "text-muted-foreground"}>
              {line.kind === "removed" ? "− " : line.kind === "added" ? "+ " : "  "}
              {line.text}
            </div>
          ))}
        </pre>
      ) : null}
    </div>
  );
}
