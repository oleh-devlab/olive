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

// The hours worth showing for these days: from the earliest thing in them to
// the latest, the present moment included, and never fewer than MIN_HOURS.
export function hourRange(segments: Segment[], now: number | null): [number, number] {
  const minutes = segments.flatMap((segment) => [segment.from, segment.to]);
  if (now !== null) minutes.push(now);
  if (!minutes.length) return QUIET_DAY;

  let from = Math.floor(Math.min(...minutes) / 60);
  let to = Math.max(Math.ceil(Math.max(...minutes) / 60), from + 1);
  if (to - from < MIN_HOURS) {
    to = Math.min(24, from + MIN_HOURS);
    from = Math.max(0, to - MIN_HOURS);
  }
  return [from, to];
}
