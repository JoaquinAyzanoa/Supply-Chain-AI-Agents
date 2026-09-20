/**
 * Web push for approvals: one button that subscribes this browser (or phone, once the
 * app is installed) to the director's notifications. The service worker shows them;
 * they carry the approval number, its summary and the link, never a mail body.
 */
import { useQuery } from "@tanstack/react-query";
import { Bell, BellOff } from "lucide-react";
import { useEffect, useState } from "react";

import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/i18n";

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

export function PushToggle({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const supported = pushSupported();
  const key = useQuery({
    queryKey: ["push", "key"],
    queryFn: async () => unwrap(await api.GET("/api/push/key")),
    enabled: supported,
    staleTime: Infinity,
  });
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    if (!supported) return;
    void currentSubscription()
      .then((sub) => setSubscribed(Boolean(sub)))
      .catch(() => setSubscribed(false));
  }, [supported]);
  if (!supported || !key.data?.enabled || !key.data.public_key) return null;
  const publicKey = key.data.public_key;

  const enable = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setMessage(t("push.denied"));
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource });
      const json = sub.toJSON();
      unwrap(await api.POST("/api/push/subscriptions", { body: { endpoint: json.endpoint ?? "", keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" } } }));
      setSubscribed(true);
      setMessage(t("push.enabled"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  const disable = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const sub = await currentSubscription();
      if (sub) {
        await api.DELETE("/api/push/subscriptions", { body: { endpoint: sub.endpoint } });
        await sub.unsubscribe();
      }
      setSubscribed(false);
      setMessage(t("push.disabled"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  const label = subscribed ? t("push.turn_off") : t("push.turn_on");
  return (
    <div className="flex flex-col gap-1">
      <Button variant="ghost" size={compact ? "icon" : "sm"} className={compact ? "" : "justify-start px-0"} aria-label={label} title={label} disabled={busy} onClick={() => void (subscribed ? disable() : enable())}>
        {subscribed ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
        {compact ? null : label}
      </Button>
      {message && !compact ? (
        <span className="text-[10px]" role="status">
          {message}
        </span>
      ) : null}
    </div>
  );
}
