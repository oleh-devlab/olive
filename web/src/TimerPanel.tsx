import { useEffect, useState } from "react";

import { botClock, clock, dayOf, minuteOf, shortDate } from "./calendar";
import {
  MAX_MIN,
  MIN_MIN,
  STEP_MIN,
  counted,
  formatClock,
  hasCounted,
  isFinished,
  isRunning,
  isTimerKey,
  loadTimer,
  minutesToLog,
  parseTimer,
  pause,
  remaining,
  saveTimer,
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

// A timer for the task being worked on, in a panel to the right of the calendar -- as
// tall as the calendar, its contents staying in sight as that scrolls -- that
// folds down to a strip. It logs what it counted against the task -- only when
// asked, and never more than it was set to -- the way `/task spend` does.
export function TimerPanel({ schedule, tasks, open, onToggle, onLog }: Props) {
  const [stored, setStored] = useState<TimerState | null>(loadTimer);
  const [now, setNow] = useState(() => Date.now());
  const [draft, setDraft] = useState<string | null>(null);
  const [logging, setLogging] = useState(false);

  const keep = (next: TimerState | null) => {
    setStored(next);
    saveTimer(next);
  };

  // Another tab changed the timer: show that one, so the same time is not counted twice.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (isTimerKey(event.key)) setStored(parseTimer(event.newValue));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const running = stored !== null && isRunning(stored);

  // Every second while it runs, to show it count -- and to stop it the moment
  // it reaches what it was set to; every half minute otherwise, which is
  // enough to notice that the schedule has moved on to another session.
  useEffect(() => {
    const timer = window.setInterval(
      () => {
        const at = Date.now();
        setNow(at);
        setStored((current) => {
          const next = current && settle(current, at);
          if (next !== current) saveTimer(next);
          return next;
        });
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
  const loggable = timer ? minutesToLog(timer, now) : 0;
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
    if (timer) keep(setMinutes(timer, minutes, Date.now()));
  }

  function commitDraft() {
    if (draft !== null && draft.trim() !== "" && Number.isFinite(Number(draft))) change(Number(draft));
    setDraft(null);
  }

  function reset() {
    if (timer && counted(timer, Date.now()) >= 60_000 && !window.confirm(`Discard the ${loggable} min counted?`)) return;
    keep(null);
  }

  async function log() {
    if (!timer || loggable < 1 || logging) return;
    const stopped = pause(timer, Date.now());
    keep(stopped);
    setLogging(true);
    try {
      if (await onLog({ id: timer.taskId, name: timer.name }, minutesToLog(stopped, Date.now()))) keep(null);
    } finally {
      setLogging(false);
    }
  }

  if (!open) {
    return (
      <aside className="panel closed" aria-label="Timer">
        <div className="panel-body">
          <button type="button" className="panel-toggle" title="Show the timer" aria-label="Show the timer" onClick={onToggle}>
            «
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
            »
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

            <div className="panel-set" role="group" aria-label="Timer length">
              <button type="button" title={`${STEP_MIN} min less`} onClick={() => change(timer.minutes - STEP_MIN)}>
                −{STEP_MIN}
              </button>
              <label>
                <input
                  type="number"
                  min={MIN_MIN}
                  max={MAX_MIN}
                  inputMode="numeric"
                  aria-label="Minutes"
                  value={draft ?? String(timer.minutes)}
                  onChange={(event) => setDraft(event.target.value)}
                  onBlur={commitDraft}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") commitDraft();
                    if (event.key === "Escape") setDraft(null);
                  }}
                />{" "}
                min
              </label>
              <button type="button" title={`${STEP_MIN} min more`} onClick={() => change(timer.minutes + STEP_MIN)}>
                +{STEP_MIN}
              </button>
            </div>

            <div className="panel-actions">
              {running ? (
                <button type="button" onClick={() => keep(pause(timer, Date.now()))}>
                  Pause
                </button>
              ) : (
                <button type="button" className="primary" disabled={finished} onClick={() => keep(start(timer, Date.now()))}>
                  {hasCounted(timer, now) ? "Resume" : "Start"}
                </button>
              )}
              <button
                type="button"
                className="primary"
                disabled={loggable < 1 || logging}
                title={`Log the time counted against #${timer.taskId}, as /task spend does`}
                onClick={() => void log()}
              >
                Log {loggable} min
              </button>
              <button type="button" className="link" disabled={!hasCounted(timer, now) && !running} onClick={reset}>
                Reset
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
