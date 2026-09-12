import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes the shadcn/ui way (later classes win). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Local date and time, in the browser's locale (dates shown to people). */
export function formatDateTime(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function formatDate(value: string | null | undefined, locale: string): string {
  if (!value) return "";
  const date = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(date);
}

export function formatMoney(value: number | null | undefined, currency: string | null | undefined, locale: string): string {
  if (value === null || value === undefined) return "";
  if (!currency) return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value);
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

export function formatNumber(value: number | null | undefined, locale: string, digits = 2): string {
  if (value === null || value === undefined) return "";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value);
}
