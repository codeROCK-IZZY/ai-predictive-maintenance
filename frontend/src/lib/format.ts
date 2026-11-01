import type { Risk } from "../api/types";

export const RISK_ORDER: Risk[] = ["low", "medium", "high", "critical"];

export const RISK_STYLE: Record<Risk, { badge: string; bar: string; hex: string }> = {
  low: { badge: "bg-emerald-50 text-emerald-800 ring-emerald-600/20", bar: "bg-emerald-500", hex: "#10b981" },
  medium: { badge: "bg-amber-50 text-amber-800 ring-amber-600/20", bar: "bg-amber-500", hex: "#f59e0b" },
  high: { badge: "bg-orange-50 text-orange-800 ring-orange-600/20", bar: "bg-orange-500", hex: "#f97316" },
  critical: { badge: "bg-red-50 text-red-800 ring-red-600/20", bar: "bg-red-600", hex: "#dc2626" },
};

export const pct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);

export const money = (v: number | string | null | undefined) =>
  v === null || v === undefined
    ? "—"
    : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

export const dateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

export const date = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—";

export const humanize = (s: string | null | undefined) => (s ? s.replaceAll("_", " ") : "—");

export function healthColor(score: number): string {
  if (score >= 90) return "bg-emerald-500";
  if (score >= 70) return "bg-amber-500";
  if (score >= 40) return "bg-orange-500";
  return "bg-red-600";
}

/** Explicit class names (Tailwind only generates classes that appear literally in source). */
export function healthStroke(score: number): string {
  if (score >= 90) return "stroke-emerald-500";
  if (score >= 70) return "stroke-amber-500";
  if (score >= 40) return "stroke-orange-500";
  return "stroke-red-600";
}
