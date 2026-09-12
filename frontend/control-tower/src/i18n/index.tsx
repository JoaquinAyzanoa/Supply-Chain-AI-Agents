/**
 * Two languages, one catalog each. `useT()` returns a translator for the
 * current language; the choice is kept in localStorage so it survives reloads.
 * Keys missing from a catalog fall back to English, then to the key itself,
 * so a forgotten translation never breaks a screen.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import en from "./messages.en.json";
import es from "./messages.es.json";

export type Language = "en" | "es";
export type MessageKey = keyof typeof en;

const CATALOGS: Record<Language, Record<string, string>> = { en, es };
const STORAGE_KEY = "control-tower.language";
export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "es", label: "Español" },
];

export function normalizeLanguage(value: string | null | undefined): Language {
  return value?.toLowerCase().startsWith("es") ? "es" : "en";
}

function initialLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return normalizeLanguage(stored);
  } catch {
    /* private mode or no storage: the default is fine */
  }
  return "en";
}

export function translate(
  language: Language,
  key: MessageKey | string,
  values?: Record<string, string | number>,
): string {
  const template = CATALOGS[language][key] ?? CATALOGS.en[key] ?? key;
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  );
}

interface I18nContextValue {
  language: Language;
  locale: string;
  setLanguage: (language: Language) => void;
  t: (key: MessageKey | string, values?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Language }) {
  const [language, setLanguageState] = useState<Language>(initial ?? initialLanguage);
  const setLanguage = useCallback((next: Language) => {
    setLanguageState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = next;
  }, []);
  const value = useMemo<I18nContextValue>(
    () => ({
      language,
      locale: language === "es" ? "es-PE" : "en-US",
      setLanguage,
      t: (key, values) => translate(language, key, values),
    }),
    [language, setLanguage],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n must be used inside I18nProvider");
  return context;
}

export function useT() {
  return useI18n().t;
}
