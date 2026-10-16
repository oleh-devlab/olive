import { useEffect, useState } from "react";

import { botClock, clock, dayOf, minuteOf, shortDate } from "./calendar";
import {
  MAX_MIN,
  MIN_MIN,
  STEP_MIN,
  formatClock,
  hasCounted,
  isFinished,
  isRunning,
  minutesToLog,
  pause,
  remaining,
  setMinutes,
  settle,
  start,
  timerFor,
  type TimerState,
} from "./timer";
import type { ScheduleEntry, SolvedSchedule, Task } from "./types";

const PAGE_TITLE = document.title;

interface Props {
  schedule: SolvedSchedule | null;
  tasks: Task[];
  open: boolean;
  onToggle: () => void;
  // Logs minutes against a task; true once they are logged.
  onLog: (task: { id: number; name: string }, minutes: number) => Promise<boolean>;
}

// The task session going on now in the schedule, or the next one to come.
function focusOf(schedule: SolvedSchedule | null, now: number): { entry: ScheduleEntry; now: boolean } | null {
  const sessions = (schedule?.items ?? []).filter((item) => item.type === "task" && item.id !== null);
  const current = sessions.find((item) => Date.parse(item.start) <= now && now < Date.parse(item.end));
  if (current) return { entry: current, now: true };
  const next = sessions.find((item) => Date.parse(item.start) > now);
  return next ? { entry: next, now: false } : null;
}

const duration = (minutes: number): string =>
  minutes >= 60 ? `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ""}` : `${minutes} min`;

// A timer for the task being worked on, in a panel beside the calendar -- as
// tall as the calendar, its contents staying in sight as that scrolls -- that
// folds down to a strip. It logs what it counted against the task -- only when
// asked, and never more than it was set to -- the way `/task spend` does.
export function TimerPanel({ schedule, tasks, open, onToggle, onLog }: Props) {
  // In this page's memory only: a reload or a closed tab drops it.
  const [stored, setStored] = useState<TimerState | null>(null);
  const [now, setNow] = useState(() => Date.now());
  // The minutes to log when set by hand, for the timer they were set for;
  // until then the field follows what the timer has counted.
  const [logSet, setLogSet] = useState<{ taskId: number; session: number; minutes: number } | null>(null);
  const [logging, setLogging] = useState(false);

  const running = stored !== null && isRunning(stored);

  // Every second while it runs, to show it count -- and to stop it the moment
  // it reaches what it was set to; every half minute otherwise, which is
  // enough to notice that the schedule has moved on to another session.
  useEffect(() => {
    const timer = window.setInterval(
      () => {
        const at = Date.now();
        setNow(at);
        setStored((current) => current && settle(current, at));
      },
      running ? 1000 : 30_000,
    );
    return () => window.clearInterval(timer);
  }, [running]);

  const focus = focusOf(schedule, now);
  // The timer shown: the stored one while it has counted anything or is set
  // for the session coming up; else a fresh one for that session.
  const sameSession =
    stored !== null && focus !== null && stored.taskId === Number(focus.entry.id) && stored.session === focus.entry.session;
  const timer =
    stored && (isRunning(stored) || hasCounted(stored, now) || sameSession)
      ? stored
      : focus
        ? timerFor(focus.entry)
        : null;

  const left = timer ? remaining(timer, now) : 0;
  const finished = timer !== null && isFinished(timer, now);
  const ownLog =
    logSet && timer && logSet.taskId === timer.taskId && logSet.session === timer.session ? logSet.minutes : null;
  const toLog = ownLog ?? (timer ? minutesToLog(timer, now) : 0);
  const ownSession = timer !== null && focus !== null && timer.taskId === Number(focus.entry.id);
  const task = timer ? tasks.find((candidate) => candidate.id === timer.taskId) : undefined;

  // The tab says how long is left while the timer runs, for when it is not in front.
  const title = !timer || (!running && !finished) ? PAGE_TITLE : `${finished ? "Time's up" : formatClock(left)} · ${timer.name}`;
  useEffect(() => {
    document.title = title;
    return () => {
      document.title = PAGE_TITLE;
    };
  }, [title]);

  function change(minutes: number) {
    if (timer) setStored(setMinutes(timer, minutes, Date.now()));
  }

  function setLog(minutes: number) {
    if (timer) setLogSet({ taskId: timer.taskId, session: timer.session, minutes: Math.min(MAX_MIN, Math.max(0, minutes)) });
  }

  function reset() {
    const discarded = timer ? minutesToLog(timer, Date.now()) : 0;
    if (discarded >= 1 && !window.confirm(`Discard the ${discarded} min counted?`)) return;
    setStored(null);
    setLogSet(null);
  }

  // Logs what the field says -- what the timer counted, or what was set by
  // hand -- and starts afresh once it is logged; the timer stops either way.
  async function log() {
    if (!timer || logging) return;
    const stopped = pause(timer, Date.now());
    const minutes = ownLog ?? minutesToLog(stopped, Date.now());
    if (minutes < 1) return;
    setStored(stopped);
    setLogging(true);
    try {
      if (await onLog({ id: timer.taskId, name: timer.name }, minutes)) {
        setStored(null);
        setLogSet(null);
      }
    } finally {
      setLogging(false);
    }
  }

  if (!open) {
    return (
      <aside className="panel closed" aria-label="Timer">
        <div className="panel-body">
          <button type="button" className="panel-toggle" title="Show the timer" aria-label="Show the timer" onClick={onToggle}>
            »
          </button>
          {timer && (running || finished || hasCounted(timer, now)) && (
            <span className={`panel-strip-time${running ? " running" : ""}`} title={timer.name}>
              {finished ? "0:00" : formatClock(left)}
            </span>
          )}
        </div>
      </aside>
    );
  }

  // In the bot's wall clock, as everywhere on the page; the date only when it is not today.
  const when = focus
    ? `${focus.now ? "Now" : "Next"} · ${
        dayOf(focus.entry.start) === botClock(now, focus.entry.start).day ? "" : `${shortDate(dayOf(focus.entry.start))} `
      }${clock(minuteOf(focus.entry.start))}–${clock(minuteOf(focus.entry.end))}`
    : "";

  return (
    <aside className="panel card" aria-label="Timer">
      <div className="panel-body">
        <div className="panel-head">
          <h2>Timer</h2>
          <button type="button" className="panel-toggle" title="Hide the timer" aria-label="Hide the timer" onClick={onToggle}>
            «
          </button>
        </div>

        {!timer ? (
          <p className="muted">No task is planned from now on.</p>
        ) : (
          <>
            <div className="panel-task">
              {ownSession && <p className="muted panel-when">{when}</p>}
              <p className="panel-name">
                <span className="muted">#{timer.taskId}</span> {timer.name}
                {timer.sessions > 1 && (
                  <span className="muted">
                    {" "}
                    {timer.session}/{timer.sessions}
                  </span>
                )}
              </p>
              <p className="muted panel-facts">
                {task ? `Break after: ${duration(task.break_duration)} · ${duration(task.duration)} left on the task` : ""}
              </p>
            </div>

            <p className={`panel-clock${finished ? " finished" : running ? " running" : ""}`} aria-live="off">
              {finished ? "Time's up" : formatClock(left)}
            </p>

            <MinutesField label="Length" value={timer.minutes} min={MIN_MIN} onChange={change} />

            <div className="panel-actions">
              {running ? (
                <button type="button" onClick={() => setStored(pause(timer, Date.now()))}>
                  Pause
                </button>
              ) : (
                <button type="button" className="primary" disabled={finished} onClick={() => setStored(start(timer, Date.now()))}>
                  {hasCounted(timer, now) ? "Resume" : "Start"}
                </button>
              )}
              <button type="button" className="link" disabled={!hasCounted(timer, now) && !running} onClick={reset}>
                Reset
              </button>
            </div>

            <div className="panel-log">
              <MinutesField label="To log" value={toLog} min={0} onChange={setLog} />
              <div className="panel-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={toLog < 1 || logging}
                  title={`Log this time against #${timer.taskId}, as /task spend does`}
                  onClick={() => void log()}
                >
                  Log {toLog} min
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}

// A number of minutes, set by the 5-minute buttons or typed in. What is typed
// counts once Enter is pressed or the field is left; Escape drops it.
function MinutesField({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (minutes: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  function commit() {
    if (draft !== null && draft.trim() !== "" && Number.isFinite(Number(draft))) onChange(Math.round(Number(draft)));
    setDraft(null);
  }

  return (
    <div className="panel-set" role="group" aria-label={label}>
      <span className="panel-set-label">{label}</span>
      <button type="button" title={`${STEP_MIN} min less`} onClick={() => onChange(Math.max(min, value - STEP_MIN))}>
        −{STEP_MIN}
      </button>
      <label>
        <input
          type="number"
          min={min}
          max={MAX_MIN}
          inputMode="numeric"
          aria-label={`${label}, minutes`}
          value={draft ?? String(value)}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") commit();
            if (event.key === "Escape") setDraft(null);
          }}
        />{" "}
        min
      </label>
      <button type="button" title={`${STEP_MIN} min more`} onClick={() => onChange(Math.min(MAX_MIN, value + STEP_MIN))}>
        +{STEP_MIN}
      </button>
    </div>
  );
}
