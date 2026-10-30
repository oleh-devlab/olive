import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { api } from "./api";
import {
  botClock,
  buildAxis,
  clip,
  clock,
  dayOf,
  hourRange,
  ENTRY_PX,
  LINE_PX,
  minuteOf,
  plannedDays,
  segmentsByDay,
  shortDate,
  weekdayOf,
  type Segment,
} from "./calendar";
import type { EntryType, ScheduleEntry, SolvedSchedule, Task } from "./types";

const SPANS = [1, 3, 7];

const KINDS: Record<EntryType, string> = {
  task: "Task",
  fixed_routine: "Fixed routine",
  flexible_routine: "Flexible routine",
  time_block: "Time block",
};

interface Props {
  // Bumped by the page whenever it has changed the data: the schedule is read again.
  version: number;
  fail: (error: unknown) => void;
  // How many days to show until the reader picks another number.
  initialSpan: number;
  // Told when the reader picks one.
  onSpanChange?: (span: number) => void;
  // Told of every schedule this reads, for the rest of the page to use the same one.
  onSolved?: (schedule: SolvedSchedule) => void;
  // The tasks as the table last read them, to name the ones that did not fit.
  tasks?: Task[];
}

export function Schedule({ version, fail, initialSpan, onSpanChange, onSolved, tasks = [] }: Props) {
  const [schedule, setSchedule] = useState<SolvedSchedule | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [span, setSpan] = useState(initialSpan);
  const [first, setFirst] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  // Counts the reader's returns to this tab, each of which reads the schedule again.
  const [returns, setReturns] = useState(0);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") setReturns((count) => count + 1);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // The cheap read: the last solve, or a new one if the data changed since.
  useEffect(() => {
    let cancelled = false;
    api<SolvedSchedule>("GET", "/api/schedule").then((solved) => {
      if (cancelled) return;
      setSchedule(solved);
      onSolved?.(solved);
    }, fail);
    return () => {
      cancelled = true;
    };
  }, [version, returns, fail, onSolved]);

  // Moves the line that marks the present moment; nothing is fetched.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  async function recalculate() {
    setRecalculating(true);
    try {
      const solved = await api<SolvedSchedule>("POST", "/api/schedule/recalculate");
      setSchedule(solved);
      onSolved?.(solved);
      setNow(Date.now());
    } catch (error) {
      fail(error);
    } finally {
      setRecalculating(false);
    }
  }

  const byDay = useMemo(() => segmentsByDay(schedule?.items ?? []), [schedule]);

  if (!schedule) {
    return (
      <section id="schedule" className="card schedule">
        <p className="muted">Loading the schedule…</p>
      </section>
    );
  }

  const sample = schedule.computed_at ?? schedule.items[0]?.start ?? null;
  const present = botClock(now, sample);
  const days = plannedDays(schedule, byDay, present.day);
  const start = Math.min(first, Math.max(days.length - span, 0));
  const shown = days.slice(start, start + span);
  const segments = shown.flatMap((day) => byDay.get(day) ?? []);
  const hours = hourRange(segments);

  const label =
    shown.length === 1
      ? `${weekdayOf(shown[0])} ${shortDate(shown[0])}`
      : `${shortDate(shown[0])} – ${shortDate(shown[shown.length - 1])}`;

  return (
    <section id="schedule" className="card schedule" aria-label="Schedule">
      <div className="schedule-bar">
        <div className="schedule-nav">
          <button
            type="button"
            title="Earlier"
            aria-label="Earlier"
            disabled={start === 0}
            onClick={() => setFirst(Math.max(0, start - span))}
          >
            ◀
          </button>
          <button type="button" disabled={start === 0} onClick={() => setFirst(0)}>
            Today
          </button>
          <button
            type="button"
            title="Later"
            aria-label="Later"
            disabled={start + span >= days.length}
            onClick={() => setFirst(start + span)}
          >
            ▶
          </button>
          <span className="schedule-range">{label}</span>
        </div>

        <div className="schedule-span" role="group" aria-label="Days shown">
          {SPANS.map((count) => (
            <button
              key={count}
              type="button"
              aria-pressed={span === count}
              onClick={() => {
                setSpan(count);
                onSpanChange?.(count);
              }}
            >
              {count === 1 ? "Day" : `${count} days`}
            </button>
          ))}
        </div>

        <div className="schedule-solve">
          <button type="button" className="primary" disabled={recalculating} onClick={() => void recalculate()}>
            {recalculating ? "Calculating…" : "Recalculate"}
          </button>
        </div>
      </div>
      <div className="schedule-facts muted">
        <span>{solveFacts(schedule)}</span>
        {schedule.status !== "NO_DATA" && <Legend />}
      </div>

      {hours ? (
        <CalendarGrid days={shown} byDay={byDay} hours={hours} present={present} />
      ) : (
        <p className="calendar-empty muted">
          Nothing is planned on {shown.length === 1 ? "this day" : "these days"}.
        </p>
      )}

      <Notes schedule={schedule} tasks={tasks} />
    </section>
  );
}

type Range = { from: number; to: number };

// The room the calendar may take before it scrolls inside itself: the screen
// less the bars above it, the day names' row included in the calendar's height.
const ROOM_MARGIN_PX = 170;
const MIN_ROOM_PX = 320;
const HEAD_PX = 26;
// The hours' column down the calendar's left (the stylesheet has the same).
const HOURS_PX = 44;
// A calendar up to this much taller than its room grows instead, the page
// scrolling for it: a scrollbar of its own would only move it a few pixels.
const GROWTH = 1.25;

interface GridProps {
  days: string[];
  byDay: Map<string, Segment[]>;
  hours: [number, number];
  present: { day: string; minute: number };
}

// The days side by side over the hours shown. It scrolls on its own, under the
// days' names, when it is taller than the screen has room for.
function CalendarGrid({ days, byDay, hours: [fromHour, toHour], present }: GridProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const range = { from: fromHour * 60, to: toHour * 60 };
  const segments = days.flatMap((day) => byDay.get(day) ?? []);

  // How wide a day is decides how many lines a name wraps to, and so how tall
  // the calendar is: measured before it is drawn.
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    setWidth(box.clientWidth);
    const observer = new ResizeObserver(() => setWidth(box.clientWidth));
    observer.observe(box);
    return () => observer.disconnect();
  }, []);
  const dayWidth = (width - HOURS_PX) / days.length;
  // A day too narrow to break a name into words, such as a week on a phone,
  // keeps it to one line, cut short.
  const narrow = dayWidth < MIN_WRAP_DAY_PX;
  const axis = buildAxis(segments, range, (segment) =>
    segment.entry.type === "time_block"
      ? LINE_PX
      : narrow
        ? ENTRY_PX
      : ENTRY_PX + (nameLines(entryLabel(segment.entry), dayWidth / segment.lanes - ENTRY_INSET_PX) - 1) * NAME_LINE_PX,
  );
  const nowShown = present.minute >= range.from && present.minute <= range.to;

  const [screen, setScreen] = useState(() => window.innerHeight);
  useEffect(() => {
    const onResize = () => setScreen(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const room = Math.max(MIN_ROOM_PX, screen - ROOM_MARGIN_PX);
  const scrolls = axis.height + HEAD_PX > room * GROWTH;

  // Brings the present moment into view whenever other days or hours are
  // shown -- not each minute as the line moves, nor on every new solve. Only
  // a calendar scrolling inside itself has anything to do here.
  const shownKey = `${days.join()} ${fromHour}-${toHour}`;
  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    const now = box.querySelector(".calendar-now");
    box.scrollTop = now
      ? now.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - box.clientHeight / 4
      : 0;
  }, [shownKey]);

  return (
    <div
      className={`calendar${scrolls ? " scrolls" : ""}${narrow ? " narrow" : ""}`}
      ref={scroller}
      style={{ "--days": days.length, maxHeight: scrolls ? room : undefined } as CSSProperties}
    >
      <div className="calendar-head">
        <span />
        {days.map((day) => (
          <span key={day} className={day === present.day ? "today" : undefined}>
            {weekdayOf(day)} {shortDate(day)}
          </span>
        ))}
      </div>

      <div className="calendar-body" style={{ height: axis.height }}>
        <div className="calendar-hours" aria-hidden="true">
          {axis.labels.map((hour) => (
            <span key={hour} style={{ top: axis.y(hour * 60) }}>
              {clock(hour * 60)}
            </span>
          ))}
        </div>
        {days.map((day) => (
          <div key={day} className="calendar-day">
            {axis.hours.map((hour) => (
              <div key={hour} className="calendar-line" style={{ top: axis.y(hour * 60) }} />
            ))}
            {axis.pieces
              .filter((piece) => piece.collapsed && piece.kind === "empty")
              .map((piece) => (
                <div
                  key={piece.from}
                  className="calendar-gap"
                  style={{ top: piece.y, height: piece.height }}
                  title={`Nothing planned ${clock(piece.from)}–${clock(piece.to)}`}
                />
              ))}
            {/* What is already past today: the schedule only plans from the moment it was solved. */}
            {day === present.day && present.minute > range.from && (
              <div className="calendar-past" style={{ height: axis.y(Math.min(present.minute, range.to)) }} />
            )}
            {(byDay.get(day) ?? []).map((segment, index) => (
              <Entry key={index} segment={segment} range={range} y={axis.y} />
            ))}
            {day === present.day && nowShown && (
              <div className="calendar-now" style={{ top: axis.y(present.minute) }} title="Now" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// What an entry's name line says around its name: its number, and which of
// its sessions it is.
function labelParts(entry: ScheduleEntry): { id: string; session: string } {
  return {
    id: entry.id !== null && entry.type !== "time_block" ? `#${entry.id} ` : "",
    session: entry.sessions > 1 ? ` ${entry.session}/${entry.sessions}` : "",
  };
}

function entryLabel(entry: ScheduleEntry): string {
  const { id, session } = labelParts(entry);
  return `${id}${entry.name || KINDS[entry.type]}${session}`;
}

// How an entry's name is set (the stylesheet has the same): its font, the
// height of a line of it, the most lines it wraps to, and the width its
// border and padding take from the day's.
const NAME_FONT = "600 12px";
const NAME_LINE_PX = 15.6;
const MAX_NAME_LINES = 3;
const ENTRY_INSET_PX = 14;
// How wide a day has to be for names to wrap in it at all.
const MIN_WRAP_DAY_PX = 90;

let measurer: CanvasRenderingContext2D | null = null;

// How many lines a name wraps to in `width` pixels, word by word as the
// browser does it, a word too long for a line broken anywhere.
function nameLines(text: string, width: number): number {
  if (!measurer) {
    measurer = document.createElement("canvas").getContext("2d");
    if (!measurer) return 1;
    measurer.font = `${NAME_FONT} ${getComputedStyle(document.body).fontFamily}`;
  }
  if (width <= 0) return 1;
  const space = measurer.measureText(" ").width;
  let lines = 1;
  let line = 0;
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const w = measurer.measureText(word).width;
    if (line > 0 && line + space + w <= width) {
      line += space + w;
      continue;
    }
    if (line > 0) lines++;
    lines += Math.ceil(w / width) - 1;
    line = w - (Math.ceil(w / width) - 1) * width;
  }
  return Math.min(Math.max(lines, 1), MAX_NAME_LINES);
}

function Entry({ segment, range, y }: { segment: Segment; range: Range; y: (minute: number) => number }) {
  // A block can reach past the hours shown; only what is inside them is drawn.
  const shown = clip(segment, range);
  if (!shown) return null;

  const { entry } = segment;
  const continued = segment.continued || shown.cutAbove;
  const continues = segment.continues || shown.cutBelow;
  const name = entry.name || KINDS[entry.type];
  const { id, session } = labelParts(entry);
  // An entry's own times, not the segment's: a block from 22:00 to 01:00 says so on both days.
  const times = `${clock(minuteOf(entry.start))}–${clock(minuteOf(entry.end))}`;

  return (
    <div
      className={`entry entry-${entry.type}${continued ? " continued" : ""}${continues ? " continues" : ""}`}
      style={{
        top: y(shown.from),
        height: y(shown.to) - y(shown.from),
        left: `${(segment.lane / segment.lanes) * 100}%`,
        width: `${100 / segment.lanes}%`,
      }}
      title={`${KINDS[entry.type]}: ${id}${name}${session}\n${times}`}
    >
      <span className="entry-name">
        {id && <span className="entry-id">{id}</span>}
        {name}
        {session && <span className="entry-session">{session}</span>}
      </span>
      <span className="entry-time">{times}</span>
    </div>
  );
}

// What the Discord message says above the schedule: when it was calculated and
// how long that took, how the solver ended, how far ahead it plans at the
// least, and how often the bot calculates it again on its own.
function solveFacts(schedule: SolvedSchedule): string {
  const facts: string[] = [];
  if (schedule.computed_at) {
    const took = schedule.solve_time > 0 ? ` in ${schedule.solve_time.toFixed(2)} s` : "";
    facts.push(`Calculated ${shortDate(dayOf(schedule.computed_at))} ${schedule.computed_at.slice(11, 16)}${took}`);
  }
  if (schedule.status) facts.push(schedule.status);
  if (schedule.planning_days > 0) facts.push(`horizon ${schedule.planning_days} ${schedule.planning_days === 1 ? "day" : "days"}`);
  if (schedule.update_seconds) facts.push(`updates every ${every(schedule.update_seconds)}`);
  return facts.join(" · ");
}

function every(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return `${Math.round(seconds)} s`;
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}

function Legend() {
  return (
    <ul className="calendar-legend" aria-label="Legend">
      {Object.entries(KINDS).map(([type, kind]) => (
        <li key={type}>
          <span className={`swatch entry-${type}`} />
          {kind}
        </li>
      ))}
    </ul>
  );
}

// What did not fit, under the calendar: one line for the tasks and the
// routines both, and nothing at all when everything did.
function Notes({ schedule, tasks }: { schedule: SolvedSchedule; tasks: Task[] }) {
  if (schedule.status === "NO_DATA") {
    return (
      <div className="calendar-notes">
        <p className="muted">Nothing to schedule yet: add a task or a routine below.</p>
      </div>
    );
  }

  const skippedTasks = schedule.skipped_task_ids.map((id) => {
    const name = tasks.find((task) => task.id === id)?.name;
    return name ? `#${id} ${name}` : `#${id}`;
  });
  if (!skippedTasks.length && !schedule.skipped_routines.length) return null;

  return (
    <div className="calendar-notes">
      <p className="warning">
        {skippedTasks.length > 0 && <span>Did not fit: {skippedTasks.join(", ")}.</span>}
        {schedule.skipped_routines.length > 0 && <span>Routines missed: {schedule.skipped_routines.join("; ")}.</span>}
      </p>
    </div>
  );
}
