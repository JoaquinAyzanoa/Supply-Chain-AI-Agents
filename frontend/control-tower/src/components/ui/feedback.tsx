import { Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import { Button } from "./button";

export function Loading() {
  const t = useT();
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground" role="status">
      <Loader2 className="h-4 w-4 animate-spin" /> {t("app.loading")}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const t = useT();
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="m-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm" role="alert">
      <p>{t("app.error", { message })}</p>
      {onRetry ? (
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          {t("app.retry")}
        </Button>
      ) : null}
    </div>
  );
}

export function Empty({ text }: { text?: string }) {
  const t = useT();
  return <p className="p-6 text-sm text-muted-foreground">{text ?? t("app.empty")}</p>;
}
