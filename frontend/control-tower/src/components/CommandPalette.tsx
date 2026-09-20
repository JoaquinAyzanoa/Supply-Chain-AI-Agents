/**
 * The command palette (Ctrl+K / Cmd+K): open any page, order or supplier, or ask the
 * assistant from anywhere. Orders come from the board already in the cache, suppliers
 * from the scorecards; both are fetched only while the palette is open.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Bot, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, unwrap } from "@/api/client";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

interface Item {
  id: string;
  label: string;
  hint: string;
  go: () => void;
}

const PAGES: { to: string; key: string }[] = [
  { to: "/", key: "nav.home" },
  { to: "/board", key: "nav.board" },
  { to: "/approvals", key: "nav.approvals" },
  { to: "/briefing", key: "nav.briefing" },
  { to: "/risk", key: "nav.risk" },
  { to: "/planning", key: "nav.planning" },
  { to: "/suppliers", key: "nav.suppliers" },
  { to: "/playbooks", key: "nav.playbooks" },
  { to: "/autonomy", key: "nav.autonomy" },
  { to: "/assistant", key: "nav.assistant" },
  { to: "/ai", key: "nav.ai" },
  { to: "/cases", key: "nav.cases" },
  { to: "/runs", key: "nav.runs" },
];

export function CommandPalette() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((o) => !o);
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (open) {
      setText("");
      setCursor(0);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  const board = useQuery({
    queryKey: ["board"],
    queryFn: async () => unwrap(await api.GET("/api/board")),
    enabled: open,
    staleTime: 60_000,
  });
  const scores = useQuery({
    queryKey: ["suppliers", "scores"],
    queryFn: async () => unwrap(await api.GET("/api/performance/scores")),
    enabled: open,
    staleTime: 60_000,
  });

  const items = useMemo<Item[]>(() => {
    const needle = text.trim().toLowerCase();
    const go = (fn: () => void) => () => {
      setOpen(false);
      fn();
    };
    const pages: Item[] = PAGES.map((page) => ({
      id: `page:${page.to}`,
      label: t(page.key),
      hint: t("palette.page"),
      go: go(() => void navigate({ to: page.to as "/" })),
    }));
    const orders: Item[] = (board.data?.cards ?? []).map((card) => ({
      id: `po:${card.po_name}`,
      label: `${card.po_name} · ${card.partner_name}`,
      hint: t("palette.order"),
      go: go(() => void navigate({ to: "/board", search: { po: card.po_name } })),
    }));
    const suppliers: Item[] = (scores.data ?? []).map((row) => ({
      id: `supplier:${row.partner_id}`,
      label: row.partner_name,
      hint: t("palette.supplier"),
      go: go(() => void navigate({ to: "/suppliers/$partnerId", params: { partnerId: String(row.partner_id) } })),
    }));
    const all = [...pages, ...orders, ...suppliers];
    const matched = needle ? all.filter((item) => item.label.toLowerCase().includes(needle)) : pages;
    const ask: Item[] = needle
      ? [
          {
            id: "ask",
            label: t("palette.ask", { text: text.trim() }),
            hint: t("nav.assistant"),
            go: go(() => void navigate({ to: "/assistant", search: { q: text.trim() } })),
          },
        ]
      : [];
    return [...matched.slice(0, 12), ...ask];
  }, [text, board.data, scores.data, navigate, t]);

  if (!open) return null;
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(items.length - 1, c + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      items[cursor]?.go();
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[10vh]" onMouseDown={() => setOpen(false)}>
      <div role="dialog" aria-modal="true" aria-label={t("palette.title")} className="w-full max-w-lg overflow-hidden rounded-lg border bg-card shadow-xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            ref={input}
            aria-label={t("palette.input")}
            className="h-11 flex-1 bg-transparent text-sm outline-none"
            placeholder={t("palette.placeholder")}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setCursor(0);
            }}
            onKeyDown={onKeyDown}
          />
          <kbd className="rounded border px-1 text-[10px] text-muted-foreground">Esc</kbd>
        </div>
        <ul role="listbox" aria-label={t("palette.results")} className="max-h-80 overflow-y-auto py-1">
          {items.map((item, index) => (
            <li key={item.id} role="option" aria-selected={index === cursor}>
              <button type="button" className={cn("flex w-full items-center gap-2 px-3 py-2 text-left text-sm", index === cursor ? "bg-accent" : "hover:bg-accent/50")} onMouseEnter={() => setCursor(index)} onClick={item.go}>
                {item.id === "ask" ? <Bot className="h-4 w-4 text-primary" /> : null}
                <span className="flex-1 truncate">{item.label}</span>
                <span className="text-xs text-muted-foreground">{item.hint}</span>
              </button>
            </li>
          ))}
          {items.length === 0 ? <li className="px-3 py-2 text-sm text-muted-foreground">{t("palette.empty")}</li> : null}
        </ul>
      </div>
    </div>
  );
}
