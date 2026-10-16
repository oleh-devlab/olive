// The arithmetic behind the calendar, apart from React.
//
// Every time here is read as the bot's wall clock: the date and the time an ISO
// string spells before its offset, which is how the tables read a deadline too.
// Days are "YYYY-MM-DD" strings and times of day are minutes since midnight.

import { WEEKDAYS } from "./dates";
import type { ScheduleEntry, SolvedSchedule } from "./types";

export const DAY_MINUTES = 24 * 60;
// So that one short item does not fill the whole height.
const MIN_HOURS = 8;
const QUIET_DAY: [number, number] = [8, 20];
// What one line of an entry's text takes, border and padding included, and
// the least and most an hour may be given to make room for it.
const LINE_PX = 22;
const MIN_HOUR_PX = 40;
const MAX_HOUR_PX = 90;

const isPlanned = (segment: Segment): boolean => segment.entry.type !== "time_block";

export const dayOf = (iso: string): string => iso.slice(0, 10);
export const minuteOf = (iso: string): number => Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16));

export function addDays(day: string, count: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}

export function weekdayOf(day: string): string {
  // getUTCDay counts from Sunday; WEEKDAYS from Monday.
  return WEEKDAYS[(new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7];
}

export const shortDate = (day: string): string => `${day.slice(8, 10)}.${day.slice(5, 7)}`;
export const clock = (minute: number): string =>
  `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;

// The bot's wall clock at `timestamp`, by the offset its solve was written in.
export function botClock(timestamp: number, sample: string | null): { day: string; minute: number } {
  const match = /([+-])(\d{2}):(\d{2})$/.exec(sample ?? "");
  const offset = match
    ? (match[1] === "-" ? -1 : 1) * (Number(match[2]) * 60 + Number(match[3]))
    : -new Date(timestamp).getTimezoneOffset();
  const shifted = new Date(timestamp + offset * 60_000).toISOString();
  return { day: dayOf(shifted), minute: minuteOf(shifted) };
}

// One day's share of an entry: an entry running past midnight is drawn on each
// day it touches. `lane` and `lanes` place it beside whatever it overlaps.
export interface Segment {
  entry: ScheduleEntry;
  from: number;
  to: number;
  continued: boolean;
  continues: boolean;
  lane: number;
  lanes: number;
}

export function segmentsByDay(entries: ScheduleEntry[]): Map<string, Segment[]> {
  const days = new Map<string, Segment[]>();

  for (const entry of entries) {
    const firstDay = dayOf(entry.start);
    const lastDay = dayOf(entry.end);
    for (let day = firstDay; day <= lastDay; day = addDays(day, 1)) {
      const from = day === firstDay ? minuteOf(entry.start) : 0;
      const to = day === lastDay ? minuteOf(entry.end) : DAY_MINUTES;
      // An entry ending exactly at midnight has nothing on the day after.
      if (to <= from) continue;

      const segment = { entry, from, to, continued: day !== firstDay, continues: day !== lastDay, lane: 0, lanes: 1 };
      days.set(day, [...(days.get(day) ?? []), segment]);
    }
  }

  for (const segments of days.values()) layOut(segments);
  return days;
}

// Overlapping segments share the day's width: each run of them that overlaps,
// directly or through one another, is split into as many lanes as it needs.
// The solver never overlaps what it places, but time blocks are the user's own.
function layOut(segments: Segment[]) {
  segments.sort((a, b) => a.from - b.from || b.to - a.to);

  let run: Segment[] = [];
  let runEnd = -1;
  const laneEnds: number[] = [];
  const closeRun = () => {
    for (const segment of run) segment.lanes = laneEnds.length;
    run = [];
    laneEnds.length = 0;
  };

  for (const segment of segments) {
    if (run.length && segment.from >= runEnd) closeRun();

    let lane = laneEnds.findIndex((end) => end <= segment.from);
    if (lane < 0) lane = laneEnds.length;
    laneEnds[lane] = segment.to;

    segment.lane = lane;
    run.push(segment);
    runEnd = run.length === 1 ? segment.to : Math.max(runEnd, segment.to);
  }
  closeRun();
}

// The days a reader can page through: from today to the end of the planning
// horizon, or to the last thing planned if the solver went further.
export function plannedDays(schedule: SolvedSchedule, segments: Map<string, Segment[]>, today: string): string[] {
  let last = addDays(today, Math.max(schedule.planning_days, 1) - 1);
  for (const day of segments.keys()) if (day > last) last = day;

  const days: string[] = [];
  for (let day = today; day <= last; day = addDays(day, 1)) days.push(day);
  return days;
}

// The hours worth showing for these days: from the earliest task or routine in
// them to the latest. Time blocks are drawn but do not stretch it -- hours that
// hold nothing but a block, such as a night's sleep, have nothing planned in
// them. Never fewer than MIN_HOURS, made up evenly above and below.
export function hourRange(segments: Segment[]): [number, number] {
  const planned = segments.filter(isPlanned);
  if (!planned.length) return QUIET_DAY;

  let from = Math.floor(Math.min(...planned.map((segment) => segment.from)) / 60);
  let to = Math.ceil(Math.max(...planned.map((segment) => segment.to)) / 60);
  const missing = MIN_HOURS - (to - from);
  if (missing > 0) {
    from = Math.max(0, from - Math.floor(missing / 2));
    to = Math.min(24, from + MIN_HOURS);
    from = Math.max(0, to - MIN_HOURS);
  }
  return [from, to];
}

// The part of a segment inside the hours shown, and whether it was cut at
// either edge -- or null when none of it is inside.
export function clip(
  segment: Segment,
  range: { from: number; to: number },
): { from: number; to: number; cutAbove: boolean; cutBelow: boolean } | null {
  const from = Math.max(segment.from, range.from);
  const to = Math.min(segment.to, range.to);
  if (to <= from) return null;
  return { from, to, cutAbove: segment.from < range.from, cutBelow: segment.to > range.to };
}

// How tall an hour is drawn: tall enough for the shortest task or routine in
// view to show its name on one line, within MIN_HOUR_PX and MAX_HOUR_PX -- an
// entry of a few minutes would otherwise stretch the day out of all proportion,
// and it still has its tooltip. The screen's own height is the stylesheet's
// business: a day that needs less than the screen is stretched to fill it.
export function hourHeight(segments: Segment[], range: { from: number; to: number }): number {
  const minutes = segments
    .filter(isPlanned)
    .map((segment) => clip(segment, range))
    .flatMap((shown) => (shown ? [shown.to - shown.from] : []));
  if (!minutes.length) return MIN_HOUR_PX;

  return Math.min(MAX_HOUR_PX, Math.max(MIN_HOUR_PX, Math.ceil((LINE_PX * 60) / Math.min(...minutes))));
}
