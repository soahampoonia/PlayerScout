// Dates from the API are Eastern-time calendar days ("2026-11-09"); never run them
// through a timezone conversion, or evening games shift a day for some users.

export function parseDay(day: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date);
}

export function longDay(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

export function shortDay(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function monthDay(day: string): string {
  return parseDay(day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function numericDay(day: string): string {
  const [, month, date] = day.split("-").map(Number);
  return `${month}/${date}`;
}

/** Tip-off in the viewer's own time zone. */
export function tipoffTime(iso: string | null): string {
  if (!iso) return "Time TBD";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function periodLabel(period: number | null): string {
  if (!period) return "Live";
  if (period <= 4) return `Q${period}`;
  return period === 5 ? "OT" : `${period - 4}OT`;
}

export function stat(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "–" : value.toFixed(digits);
}

export function signed(value: number): string {
  return value > 0 ? `+${value}` : `${value}`;
}

/** 0.475 -> "48%". Market prices are in whole cents, so one decimal would be false precision. */
export function pct(p: number | null | undefined): string {
  return p === null || p === undefined ? "–" : `${Math.round(p * 100)}%`;
}

/** Two sides of one market as whole percents that add up to 100 (0.325 -> 33% and 67%, not 68%). */
export function pctPair(first: number): [string, string] {
  const a = Math.round(first * 100);
  return [`${a}%`, `${100 - a}%`];
}

/** -2.5 -> "−2.5", 3.5 -> "+3.5" (a real minus sign lines up with the plus). */
export function spreadLine(line: number | null): string {
  if (line === null) return "–";
  return line > 0 ? `+${line}` : line < 0 ? `−${Math.abs(line)}` : "PK";
}

/** 428215 -> "$428K", 9.18 -> "$9". */
export function dollars(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `$${Math.round(value / 1000)}K`;
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}K`;
  return `$${Math.round(value)}`;
}

/** "2 min ago" from an ISO timestamp. */
export function ago(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

export const THIN_HINT = "Thin market: a wide bid/ask spread or little trading in the last 24 hours, so the price says less.";
