// A deadline and a block's bounds come as ISO with the bot's own offset
// ("2026-10-25T18:00:00+02:00"). Their first 16 characters are the bot's
// wall-clock time, which is what a datetime input holds and what the API takes
// back. A routine's times are "HH:MM" and its skip date "DD.MM.YYYY".

import type { Routine } from "./types";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const wallClock = (iso: string | null | undefined): string => (iso ? iso.slice(0, 16) : "");
export const timeOf = (iso: string | null | undefined): string => (iso ? iso.slice(11, 16) : "");

// The year only when it is not this one: the tables are narrower without it.
export function displayDateTime(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  if (!m) return "—";
  const year = Number(m[1]) === new Date().getFullYear() ? "" : `.${m[1]}`;
  return `${m[3]}.${m[2]}${year} ${m[4]}:${m[5]}`;
}

export function isPast(iso: string | null | undefined): boolean {
  return Boolean(iso) && new Date(iso as string) < new Date();
}

export function localToday(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// "DD.MM.YYYY" as stored, and the "YYYY-MM-DD" a date input holds.
export function isoDate(stored: string | null | undefined): string {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(stored || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}

export function storedDate(iso: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : null;
}

export function describeRepeat(repeat: string, weekdays: number[] | null | undefined): string {
  if (repeat === "weekly") return (weekdays || []).map((d) => WEEKDAYS[d]).join(", ");
  return repeat === "once" ? "Once" : "Daily";
}

// A skip whose date has passed no longer leaves anything out.
export function skippedUntil(routine: Routine): string {
  const until = isoDate(routine.resume_after);
  return until && until >= localToday() && routine.resume_after ? routine.resume_after : "—";
}
