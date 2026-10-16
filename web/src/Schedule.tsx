import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { api } from "./api";
import {
  botClock,
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
} from "./calendar";
import type { EntryType, SolvedSchedule } from "./types";

const SPANS = [1, 3, 7];

const KINDS: Record<EntryType, string> = {
  task: "Task",
  fixed_routine: "Fixed routine",
  flexible_routine: "Flexible routine",
  time_block: "Time block",
};

// As many days as fit side by side without squeezing a day's names into nothing.
function spanForScreen(): number {
  if (window.innerWidth < 700) return 1;
  return window.innerWidth < 1100 ? 3 : 7;
}

interface Props {
  // Bumped by the page whenever it has changed the data: the schedule is read again.
  version: number;
  fail: (error: unknown) => void;
}

export function Schedule({ version, fail }: Props) {
  const [schedule, setSchedule] = useState<SolvedSchedule | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [span, setSpan] = useState(spanForScreen);
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
      if (!cancelled) setSchedule(solved);
    }, fail);
    return () => {
      cancelled = true;
    };
  }, [version, returns, fail]);

  // Moves the line that marks the present moment; nothing is fetched.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  async function recalculate() {
    setRecalculating(true);
    try {
      setSchedule(await api<SolvedSchedule>("POST", "/api/schedule/recalculate"));
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
  const [fromHour, toHour] = hourRange(segments);
  const range = { from: fromHour * 60, to: toHour * 60 };

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
            <button key={count} type="button" aria-pressed={span === count} onClick={() => setSpan(count)}>
              {count === 1 ? "Day" : `${count} days`}
            </button>
          ))}
        </div>

        <div className="schedule-solve">
          <span className="muted" title={`${schedule.status} · solved in ${schedule.solve_time.toFixed(2)} s`}>
            {schedule.computed_at
              ? `Calculated ${shortDate(dayOf(schedule.computed_at))} ${schedule.computed_at.slice(11, 16)}`
              : ""}
          </span>
          <button type="button" className="primary" disabled={recalculating} onClick={() => void recalculate()}>
            {recalculating ? "Calculating…" : "Recalculate"}
          </button>
        </div>
      </div>

      <div className="calendar" style={{ "--days": shown.length, "--hours": toHour - fromHour } as CSSProperties}>
        <div className="calendar-head">
          <span />
          {shown.map((day) => (
            <span key={day} className={day === present.day ? "today" : undefined}>
              {weekdayOf(day)} {shortDate(day)}
            </span>
          ))}
        </div>

        <div className="calendar-body">
          <div className="calendar-hours" aria-hidden="true">
            {Array.from({ length: toHour - fromHour }, (_, index) => (
              <span key={index}>{clock((fromHour + index) * 60)}</span>
            ))}
          </div>
          {shown.map((day) => (
            <div key={day} className="calendar-day">
              {/* What is already past today: the schedule only plans from the moment it was solved. */}
              {day === present.day && present.minute > range.from && (
                <div
                  className="calendar-past"
                  style={{ height: share(Math.min(present.minute, range.to) - range.from, range) }}
                />
              )}
              {(byDay.get(day) ?? []).map((segment, index) => (
                <Entry key={index} segment={segment} range={range} />
              ))}
              {day === present.day && present.minute >= range.from && present.minute <= range.to && (
                <div className="calendar-now" style={{ top: percent(present.minute, range) }} title="Now" />
              )}
            </div>
          ))}
        </div>
      </div>

      <Notes schedule={schedule} />
    </section>
  );
}

type Range = { from: number; to: number };

// How far down the day a moment is, and how much of it a stretch of minutes takes.
const percent = (minute: number, range: Range): string => share(minute - range.from, range);
const share = (minutes: number, range: Range): string => `${(minutes / (range.to - range.from)) * 100}%`;

function Entry({ segment, range }: { segment: Segment; range: Range }) {
  // A block can reach past the hours shown; only what is inside them is drawn.
  const shown = clip(segment, range);
  if (!shown) return null;

  const { entry } = segment;
  const { from, to } = shown;
  const continued = segment.continued || shown.cutAbove;
  const continues = segment.continues || shown.cutBelow;
  const name = entry.name || KINDS[entry.type];
  // An entry's own times, not the segment's: a block from 22:00 to 01:00 says so on both days.
  const times = `${clock(minuteOf(entry.start))}–${clock(minuteOf(entry.end))}`;
  const session = entry.sessions > 1 ? ` ${entry.session}/${entry.sessions}` : "";
  const id = entry.id !== null && entry.type !== "time_block" ? `#${entry.id} ` : "";

  return (
    <div
      className={`entry entry-${entry.type}${continued ? " continued" : ""}${continues ? " continues" : ""}`}
      style={{
        top: percent(from, range),
        height: share(to - from, range),
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

function Notes({ schedule }: { schedule: SolvedSchedule }) {
  const legend = (
    <ul className="calendar-legend" aria-label="Legend">
      {Object.entries(KINDS).map(([type, kind]) => (
        <li key={type}>
          <span className={`swatch entry-${type}`} />
          {kind}
        </li>
      ))}
    </ul>
  );

  if (schedule.status === "NO_DATA") {
    return (
      <div className="calendar-notes">
        <p className="muted">Nothing to schedule yet: add a task or a routine below.</p>
      </div>
    );
  }

  return (
    <div className="calendar-notes">
      {legend}
      {schedule.skipped_task_ids.length > 0 && (
        <p className="warning">
          Did not fit: {schedule.skipped_task_ids.length === 1 ? "task" : "tasks"}{" "}
          {schedule.skipped_task_ids.map((id) => `#${id}`).join(", ")}.
        </p>
      )}
      {schedule.skipped_routines.length > 0 && (
        <p className="warning">Routines missed: {schedule.skipped_routines.join("; ")}.</p>
      )}
    </div>
  );
}
