// The arithmetic behind the calendar, apart from React.
//
// Every time here is read as the bot's wall clock: the date and the time an ISO
// string spells before its offset, which is how the tables read a deadline too.
// Days are "YYYY-MM-DD" strings and times of day are minutes since midnight.

import { WEEKDAYS } from "./dates";
import type { ScheduleEntry, SolvedSchedule } from "./types";

export const DAY_MINUTES = 24 * 60;
// What an entry's text takes, border and padding included: one line, and the
// two -- its name, then its times -- of a task or a routine whose name fits on
// one line. A longer name may say how much more it needs (buildAxis, `need`).
export const LINE_PX = 22;
export const ENTRY_PX = 36;
// What an hour adds to the height on top of that, so that a long entry still
// looks longer than a short one.
const HOUR_PX = 24;
// What a collapsed stretch is drawn as: a thin band when it is empty, and one
// line when a time block is in it, so that the block still shows its name.
const GAP_PX = 10;
const BLOCKED_PX = LINE_PX;
// How far below the last hour's label the next one has to be to fit.
const LABEL_PX = 13;

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

// The hours worth showing for these days: from the hour of the earliest task
// or routine in them to that of the latest. Time blocks are drawn but do not
// stretch it -- hours that hold nothing but a block, such as a night's sleep,
// have nothing planned in them. Null when nothing is planned on these days.
export function hourRange(segments: Segment[]): [number, number] | null {
  const planned = segments.filter(isPlanned);
  if (!planned.length) return null;

  const from = Math.floor(Math.min(...planned.map((segment) => segment.from)) / 60);
  const to = Math.ceil(Math.max(...planned.map((segment) => segment.to)) / 60);
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

// A stretch of the hours shown, as it is drawn: from `y` down, `height` tall.
// One that is `collapsed` is drawn shorter than its minutes would be.
export interface Piece {
  from: number;
  to: number;
  kind: "planned" | "blocked" | "empty";
  y: number;
  height: number;
  collapsed: boolean;
}

export interface Axis {
  pieces: Piece[];
  height: number;
  // Where a moment of the day is drawn, in pixels from the top.
  y: (minute: number) => number;
  // The hours that get a line: not those inside a collapsed stretch.
  hours: number[];
  // Those of them that also get a label: not the last, nor one that would
  // crowd the one above -- the two edges of a collapsed hour, say.
  labels: number[];
}

// How the hours shown are laid out from top to bottom. Each entry is as tall
// as its text needs -- a task or routine its name, over as many lines as
// `need` says it wraps to, and its times; a time block its name -- plus
// HOUR_PX an hour, so length still shows. A stretch with nothing in it on any
// of the days shown -- a break, free time -- collapses to a thin band, and one
// with only time blocks in it to a single line. The times are on the entries,
// which is what lets the axis be uneven.
export function buildAxis(
  segments: Segment[],
  range: { from: number; to: number },
  need: (segment: Segment) => number = (segment) => (isPlanned(segment) ? ENTRY_PX : LINE_PX),
): Axis {
  const shown = segments.flatMap((segment) => {
    const part = clip(segment, range);
    return part ? [{ planned: isPlanned(segment), need: need(segment), from: part.from, to: part.to }] : [];
  });
  const edges = [...new Set([range.from, range.to, ...shown.flatMap((part) => [part.from, part.to])])].sort(
    (a, b) => a - b,
  );

  // Every stretch between two edges is one kind all through. Neighbours of a
  // kind are joined into one band when collapsed; planned ones are kept apart,
  // since an entry's height is spread over exactly the stretches it covers.
  const pieces: Piece[] = [];
  for (let index = 0; index + 1 < edges.length; index++) {
    const [from, to] = [edges[index], edges[index + 1]];
    const covering = shown.filter((part) => part.from < to && part.to > from);
    const kind = covering.some((part) => part.planned) ? "planned" : covering.length ? "blocked" : "empty";
    const last = pieces[pieces.length - 1];
    if (kind !== "planned" && last?.kind === kind) last.to = to;
    else pieces.push({ from, to, kind, y: 0, height: 0, collapsed: false });
  }

  const scaled = (piece: Piece): number => ((piece.to - piece.from) * HOUR_PX) / 60;
  for (const piece of pieces) {
    const cap = piece.kind === "empty" ? GAP_PX : piece.kind === "blocked" ? BLOCKED_PX : Infinity;
    piece.height = Math.min(scaled(piece), cap);
    piece.collapsed = piece.height < scaled(piece);
  }

  // Then every entry gets the room its text needs, spread over the stretches
  // it covers. One it covers only part of -- a band joining two time blocks --
  // passes on only that part of what it is given, and so is given more. Room
  // given only adds, so an entry that had enough keeps it whatever comes after.
  const length = (piece: Piece): number => piece.to - piece.from;
  for (const part of shown) {
    const minutes = part.to - part.from;
    const covered = pieces.filter((piece) => piece.from < part.to && piece.to > part.from);
    const overlap = (piece: Piece): number => Math.min(piece.to, part.to) - Math.max(piece.from, part.from);
    const height = covered.reduce((sum, piece) => sum + (piece.height * overlap(piece)) / length(piece), 0);
    const short = part.need - height;
    if (short <= 0) continue;
    for (const piece of covered) piece.height += (short * length(piece)) / minutes;
  }

  let y = 0;
  for (const piece of pieces) {
    piece.y = y;
    y += piece.height;
  }

  const yOf = (minute: number): number => {
    const piece = pieces.find((candidate) => minute <= candidate.to) ?? pieces[pieces.length - 1];
    if (!piece) return 0;
    const share = Math.min(1, Math.max(0, (minute - piece.from) / (piece.to - piece.from)));
    return piece.y + share * piece.height;
  };

  const hours: number[] = [];
  for (let hour = Math.ceil(range.from / 60); hour * 60 <= range.to; hour++) {
    const minute = hour * 60;
    if (!pieces.some((piece) => piece.collapsed && piece.from < minute && minute < piece.to)) hours.push(hour);
  }

  const labels: number[] = [];
  for (const hour of hours) {
    const previous = labels[labels.length - 1];
    if (hour * 60 < range.to && (previous === undefined || yOf(hour * 60) - yOf(previous * 60) >= LABEL_PX)) {
      labels.push(hour);
    }
  }

  return { pieces, height: y, y: yOf, hours, labels };
}
