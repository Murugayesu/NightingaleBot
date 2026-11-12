import { t } from "./strings";

export type DateGroup = "today" | "week" | "month" | "older";

/** Sidebar group for a timestamp, relative to `now`. */
export function dateGroup(iso: string, now: Date = new Date()): DateGroup {
  const then = new Date(iso);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (then >= startOfToday) return "today";
  const days = (startOfToday.getTime() - then.getTime()) / 86_400_000;
  if (days < 7) return "week";
  if (days < 30) return "month";
  return "older";
}

export const groupLabel: Record<DateGroup, string> = {
  today: t.sidebar.today,
  week: t.sidebar.week,
  month: t.sidebar.month,
  older: t.sidebar.older,
};

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** A short title from the first question (guest chats, before the server names them). */
export function titleFrom(question: string): string {
  const text = question.replace(/\s+/g, " ").trim();
  if (text.length <= 60) return text || "New chat";
  const cut = text.slice(0, 57);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 20 ? cut.lastIndexOf(" ") : 57).replace(/[ ,.;:]+$/, "")}…`;
}

export function percent(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}
