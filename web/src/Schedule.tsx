import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import { api } from "./api";
import {
  botClock,
  buildAxis,
  clip,
  clock,
  dayOf,
  hourRange,
  minuteOf,
  plannedDays,
  segmentsByDay,
  shortDate,
  weekdayOf,
  type Segment,
  type Spacing,
} from "./calendar";
import type { EntryType, ScheduleEntry, SolvedSchedule, Task } from "./types";

const SPANS = [1, 3, 7];

const KINDS: Record<EntryType, string> = {
  task: "Task",
  fixed_routine: "Fixed routine",
  flexible_routine: "Flexible routine",
  time_block: "Time block",
};

// The legend says less, to fit in the toolbar: beside its colours, "Fixed" and
// "Flexible" can only be routines. Its tooltips say the whole name.
const LEGEND: Record<EntryType, string> = {
  task: "Task",
  fixed_routine: "Fixed",
  flexible_routine: "Flexible",
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
  // Handed the place at the start of the toolbar where the folded timer goes.
  toolbarSlot?: (element: HTMLElement | null) => void;
  // Whether a calendar much taller than the screen scrolls inside itself until
  // the reader says otherwise, and who is told when they do.
  initialScroll?: boolean;
  onScrollChange?: (on: boolean) => void;
  // A place in the page's header, under the user's name, where how far ahead
  // the schedule plans goes when that leaves the toolbar fewer rows.
  headerSlot?: HTMLElement | null;
}

export function Schedule({
  version,
  fail,
  initialSpan,
  onSpanChange,
  onSolved,
  tasks = [],
  toolbarSlot,
  initialScroll = true,
  onScrollChange,
  headerSlot = null,
}: Props) {
  const [schedule, setSchedule] = useState<SolvedSchedule | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [span, setSpan] = useState(initialSpan);
  const [first, setFirst] = useState(0);
  const [scrollInside, setScrollInside] = useState(initialScroll);
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

  // When and how the schedule was solved goes in the toolbar, where it stays
  // only while it fits there whole; else it takes a line of its own below.
  // Hidden rather than taken out, it keeps its place, so the measure holds.
  const facts = schedule ? solveFacts(schedule) : "";
  const factsInBar = useRef<HTMLSpanElement>(null);
  const [factsFit, setFactsFit] = useState(true);
  useLayoutEffect(() => {
    const box = factsInBar.current;
    if (!box) return;
    const check = () => setFactsFit(box.scrollWidth <= box.clientWidth + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(box);
    return () => observer.disconnect();
  }, [facts]);

  // How far ahead it plans and how often it is solved again go beside the
  // button that recalculates, unless putting them in the header -- a line
  // more there -- leaves the toolbar a row fewer, or its facts room to fit
  // in it. Worked out from what each part is as wide as, which does not
  // depend on where they are, so the choice stays put.
  const plan = schedule ? planFacts(schedule) : "";
  const bar = useRef<HTMLDivElement>(null);
  const [planInHeader, setPlanInHeader] = useState(false);
  useLayoutEffect(() => {
    const box = bar.current;
    if (!box || !headerSlot) return;
    const decide = () => setPlanInHeader(headerSavesRoom(box));
    decide();
    const observer = new ResizeObserver(decide);
    observer.observe(box);
    return () => observer.disconnect();
  }, [facts, plan, headerSlot]);

  // The folded timer's place is there from the start: a schedule that never
  // loads must not take the way to the timer with it.
  if (!schedule) {
    return (
      <section id="schedule" className="card schedule">
        <div className="schedule-bar">
          <div className="schedule-nav">
            <span className="timer-slot" ref={toolbarSlot} />
            <span className="muted">Loading the schedule…</span>
          </div>
        </div>
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

  const planned = <span className="plan-facts muted">{plan}</span>;

  return (
    <section id="schedule" className="card schedule" aria-label="Schedule">
      {planInHeader && headerSlot && createPortal(planned, headerSlot)}
      <div className="schedule-bar" ref={bar}>
        <div className="schedule-nav">
          <span className="timer-slot" ref={toolbarSlot} />
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

        <span
          ref={factsInBar}
          className="schedule-facts-bar muted"
          style={factsFit ? undefined : { visibility: "hidden" }}
          aria-hidden={!factsFit}
        >
          {facts}
        </span>

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
          <button
            type="button"
            className="schedule-scroll"
            aria-pressed={scrollInside}
            title="Let a calendar much taller than the screen scroll inside itself, its day names in sight; off, the page scrolls instead"
            onClick={() => {
              setScrollInside(!scrollInside);
              onScrollChange?.(!scrollInside);
            }}
          >
            Scroll
          </button>
        </div>

        {schedule.status !== "NO_DATA" && <Legend />}

        <div className="schedule-solve">
          {!planInHeader && planned}
          <button type="button" className="primary" disabled={recalculating} onClick={() => void recalculate()}>
            {recalculating ? "Calculating…" : "Recalculate"}
          </button>
        </div>
      </div>
      {!factsFit && <p className="schedule-facts muted">{facts}</p>}

      {hours ? (
        <CalendarGrid days={shown} byDay={byDay} hours={hours} present={present} scrollInside={scrollInside} />
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

// What the choice weighs, roughly: a row of the toolbar and the gap above it,
// the line its facts take below it when they do not fit in it, and the line
// the header grows by. And the gaps the stylesheet puts between the parts.
const BAR_ROW_PX = 32;
const FACTS_LINE_PX = 21;
const HEADER_LINE_PX = 17;
const BAR_GAP_PX = 14;
const SOLVE_GAP_PX = 6;

// Whether the toolbar takes less room, header included, with how far ahead
// the schedule plans in the header. It lays the toolbar's parts out the way
// the stylesheet wraps them, both ways: the facts take what is left of the
// first row and fit there or go below; nothing else shrinks.
function headerSavesRoom(bar: HTMLElement): boolean {
  const widthOf = (selector: string): number => {
    const element = document.querySelector(selector);
    return element ? element.getBoundingClientRect().width : 0;
  };
  const plan = document.querySelector(".plan-facts");
  const facts = bar.querySelector(".schedule-facts-bar");
  if (!plan || !facts) return false;
  // Its box, not its scrollWidth: in the header it is inline, which has none.
  const planWidth = plan.getBoundingClientRect().width;
  const recalculate = widthOf(".schedule-solve button");
  const parts = [".schedule-nav", ".schedule-span", ".schedule-bar .calendar-legend"].map(widthOf).filter(Boolean);

  const cost = (solve: number, header: boolean): number => {
    // The facts come second and take no width of their own until laid out.
    const widths = [parts[0], 0, ...parts.slice(1), solve];
    let rows = 1;
    let used = 0;
    let firstRowLeft = 0;
    for (const [index, width] of widths.entries()) {
      const needed = used === 0 && index === 0 ? width : used + BAR_GAP_PX + width;
      if (index > 0 && needed > bar.clientWidth) {
        if (rows === 1) firstRowLeft = bar.clientWidth - used;
        rows += 1;
        used = width;
      } else {
        used = needed;
      }
    }
    if (rows === 1) firstRowLeft = bar.clientWidth - used;
    const factsFit = firstRowLeft >= facts.scrollWidth;
    return rows * BAR_ROW_PX + (factsFit ? 0 : FACTS_LINE_PX) + (header ? HEADER_LINE_PX : 0);
  };

  return cost(recalculate, true) < cost(planWidth + SOLVE_GAP_PX + recalculate, false);
}

// The room the calendar has on the screen: the window from where the
// calendar starts down, less what goes below it -- the line of what did not
// fit and the card's edge -- the day names' row included in its height.
const BELOW_PX = 34;
const MIN_ROOM_PX = 320;
const HEAD_PX = 26;
// The hours' column down the calendar's left (the stylesheet has the same).
const HOURS_PX = 36;
// A calendar up to this much taller than its room grows instead, the page
// scrolling for it: a scrollbar of its own would only move it a few pixels.
const GROWTH = 1.25;

// How densely the calendar is set, from the most comfortable on: each step
// a little tighter than the last -- thinner bands, less height an hour, then
// lines closer together and smaller times. The calendar takes the first one
// it fits on the screen at, and the last when it fits at none. The text's
// heights follow from the fonts (the stylesheet sets them from these): a
// 12 px name, border and padding 4 px, and the times below it.
interface Density extends Spacing {
  lineHeight: number;
  timePx: number;
}

const DENSITIES: Density[] = [
  { hourPx: 24, gapPx: 8, lineHeight: 1.2, timePx: 10.5, linePx: 20 },
  { hourPx: 16, gapPx: 7, lineHeight: 1.2, timePx: 10.5, linePx: 20 },
  { hourPx: 10, gapPx: 6, lineHeight: 1.15, timePx: 10, linePx: 19 },
  { hourPx: 6, gapPx: 5, lineHeight: 1.15, timePx: 10, linePx: 19 },
];

const nameLinePx = (density: Density): number => NAME_PX * density.lineHeight;
const entryPx = (density: Density): number =>
  Math.ceil(ENTRY_BOX_PX + nameLinePx(density) + density.timePx * density.lineHeight);

interface GridProps {
  days: string[];
  byDay: Map<string, Segment[]>;
  hours: [number, number];
  present: { day: string; minute: number };
  // Whether it may scroll inside itself when much taller than its room.
  scrollInside: boolean;
}

// The days side by side over the hours shown. It scrolls on its own, under the
// days' names, when it is taller than the screen has room for.
function CalendarGrid({ days, byDay, hours: [fromHour, toHour], present, scrollInside }: GridProps) {
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

  // Where the calendar starts on the page, and how tall the window is: what
  // is left between them is its room.
  const [screen, setScreen] = useState(() => ({ height: window.innerHeight, top: 0 }));
  useLayoutEffect(() => {
    const measure = () => {
      const box = scroller.current;
      if (box) setScreen({ height: window.innerHeight, top: box.getBoundingClientRect().top + window.scrollY });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  const room = Math.max(MIN_ROOM_PX, screen.height - screen.top - BELOW_PX);

  // Each task or routine laid out at a density: the room the axis makes for
  // it, and what the entry clamps its name to, so its times stay in sight.
  const layOut = (density: Density) => {
    const layouts = new Map<Segment, Layout>();
    for (const segment of segments) {
      if (segment.entry.type !== "time_block") {
        const space = dayWidth / segment.lanes - ENTRY_INSET_PX;
        layouts.set(segment, layOutEntry(segment.entry, space, narrow, density.timePx));
      }
    }
    const axis = buildAxis(
      segments,
      range,
      (segment) => {
        const layout = layouts.get(segment);
        if (!layout || layout.inline) return density.linePx;
        return entryPx(density) + (layout.lines - 1) * nameLinePx(density);
      },
      density,
    );
    return { density, layouts, axis };
  };
  // The most comfortable density the calendar fits on the screen at.
  let laid = layOut(DENSITIES[0]);
  for (const density of DENSITIES.slice(1)) {
    if (laid.axis.height + HEAD_PX <= room) break;
    laid = layOut(density);
  }
  const { density, layouts, axis } = laid;
  const nowShown = present.minute >= range.from && present.minute <= range.to;
  const scrolls = scrollInside && axis.height + HEAD_PX > room * GROWTH;

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
      style={
        {
          "--days": days.length,
          "--entry-line": density.lineHeight,
          "--time-size": `${density.timePx}px`,
          maxHeight: scrolls ? room : undefined,
        } as CSSProperties
      }
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
              <Entry key={index} segment={segment} range={range} y={axis.y} layout={layouts.get(segment)} />
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

// An entry's own times, not the segment's: a block from 22:00 to 01:00 says so on both days.
const timesOf = (entry: ScheduleEntry): string => `${clock(minuteOf(entry.start))}–${clock(minuteOf(entry.end))}`;

// How an entry's name is set (the stylesheet has the same): its fonts -- the
// name bold, its number and session not, its times smaller -- the height of a
// line of it, the most lines it wraps to, the width its border and padding
// take from the day's, and the gap before its times when they share its line.
const NAME_PX = 12;
const NAME_FONT = `600 ${NAME_PX}px`;
const LABEL_FONT = `400 ${NAME_PX}px`;
const INLINE_GAP_PX = 6;
// An entry's border and padding, above and below its text.
const ENTRY_BOX_PX = 4;
// What a measured line is short of what the browser sets, at most: rounding.
const SLACK_PX = 2;

// The page sets its numbers -- an entry's number, session and times -- in
// tabular figures, each as wide as a 0, which a canvas cannot be told to
// use: it measures them as zeros instead.
const tabular = (text: string): string => text.replace(/[0-9]/g, "0");
const MAX_NAME_LINES = 3;
const ENTRY_INSET_PX = 14;
// How wide a day has to be for names to wrap in it at all.
const MIN_WRAP_DAY_PX = 90;

let measurer: CanvasRenderingContext2D | null = null;
let family = "";

// How a task or routine is laid out in `width` pixels: on one line with its
// times after its name when the whole of both fit there, else its name over
// as many lines as it wraps to and its times below.
interface Layout {
  inline: boolean;
  lines: number;
}

// The name wraps word by word as the browser does it, a word too long for a
// line broken anywhere. Only an estimate: the entry clamps the name to it, so
// a browser that needs more room cuts the name short rather than pushing its
// times out of sight. A day too narrow to wrap in keeps it to one line.
function layOutEntry(entry: ScheduleEntry, width: number, narrow: boolean, timePx: number): Layout {
  if (!measurer) {
    measurer = document.createElement("canvas").getContext("2d");
    if (!measurer) return { inline: false, lines: 1 };
    family = getComputedStyle(document.body).fontFamily;
  }
  if (width <= 0) return { inline: false, lines: 1 };
  const { id, session } = labelParts(entry);
  const words = (text: string, font: string) =>
    text
      .split(/\s+/)
      .filter(Boolean)
      .map((word) => ({ word, font: `${font} ${family}` }));
  const all = [
    ...words(tabular(id), LABEL_FONT),
    ...words(entry.name || KINDS[entry.type], NAME_FONT),
    ...words(tabular(session), LABEL_FONT),
  ];
  width -= SLACK_PX;

  let lines = 1;
  let line = 0;
  for (const { word, font } of all) {
    measurer.font = font;
    const space = measurer.measureText(" ").width;
    const w = measurer.measureText(word).width;
    if (line > 0 && line + space + w <= width) {
      line += space + w;
      continue;
    }
    if (line > 0) lines++;
    lines += Math.ceil(w / width) - 1;
    line = w - (Math.ceil(w / width) - 1) * width;
  }

  measurer.font = `400 ${timePx}px ${family}`;
  const inline = lines === 1 && line + INLINE_GAP_PX + measurer.measureText(tabular(timesOf(entry))).width <= width;
  return { inline, lines: narrow ? 1 : Math.min(Math.max(lines, 1), MAX_NAME_LINES) };
}

interface EntryProps {
  segment: Segment;
  range: Range;
  y: (minute: number) => number;
  // How the calendar made room for it; none for a time block.
  layout?: Layout;
}

function Entry({ segment, range, y, layout }: EntryProps) {
  // A block can reach past the hours shown; only what is inside them is drawn.
  const shown = clip(segment, range);
  if (!shown) return null;

  const { entry } = segment;
  const continued = segment.continued || shown.cutAbove;
  const continues = segment.continues || shown.cutBelow;
  const name = entry.name || KINDS[entry.type];
  const { id, session } = labelParts(entry);
  const times = timesOf(entry);
  const classes = [
    "entry",
    `entry-${entry.type}`,
    continued && "continued",
    continues && "continues",
    layout?.inline && "inline",
  ].filter(Boolean);

  return (
    <div
      className={classes.join(" ")}
      style={{
        top: y(shown.from),
        height: y(shown.to) - y(shown.from),
        left: `${(segment.lane / segment.lanes) * 100}%`,
        width: `${100 / segment.lanes}%`,
        "--name-lines": layout?.lines ?? 1,
      } as CSSProperties}
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

// What the Discord message says above the schedule, in two parts: when it was
// calculated and how long that took, and how the solver ended --
function solveFacts(schedule: SolvedSchedule): string {
  const facts: string[] = [];
  if (schedule.computed_at) {
    const took = schedule.solve_time > 0 ? ` in ${schedule.solve_time.toFixed(2)} s` : "";
    facts.push(`Calculated ${shortDate(dayOf(schedule.computed_at))} ${schedule.computed_at.slice(11, 16)}${took}`);
  }
  if (schedule.status) facts.push(schedule.status);
  return facts.join(" · ");
}

// -- and, beside the button that recalculates it, how far ahead it plans at
// the least and how often the bot calculates it again on its own.
function planFacts(schedule: SolvedSchedule): string {
  const facts: string[] = [];
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
      {(Object.keys(KINDS) as EntryType[]).map((type) => (
        <li key={type} title={KINDS[type]}>
          <span className={`swatch entry-${type}`} />
          {LEGEND[type]}
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
