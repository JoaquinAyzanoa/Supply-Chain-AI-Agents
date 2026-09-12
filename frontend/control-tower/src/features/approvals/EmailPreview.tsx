/**
 * The supplier email as the approver will send it, sanitised: scripts, forms,
 * frames and every image are dropped (no remote tracking pixels load from the
 * inbox), links open in a new tab. Editing shows the raw HTML; most edits are a
 * sentence or a number, and the Outlook draft is patched with exactly this.
 */
import DOMPurify from "dompurify";
import { useMemo } from "react";

const purifier = DOMPurify();
purifier.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

export function sanitizeEmailHtml(html: string): string {
  return purifier.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "picture", "svg", "iframe", "object", "embed", "video", "audio", "form", "input", "button", "style", "link", "meta"],
    FORBID_ATTR: ["background", "srcset", "onerror", "onload"],
  });
}

export function EmailPreview({ html }: { html: string }) {
  const clean = useMemo(() => sanitizeEmailHtml(html), [html]);
  return (
    <div
      data-testid="email-preview"
      className="prose prose-sm max-w-none rounded-md border bg-white p-3 text-sm [&_a]:text-primary [&_a]:underline [&_p]:my-1"
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
