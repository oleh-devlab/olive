// The timer for the task being worked on, apart from React.
//
// What it counts is kept as a start time and what was counted before the last
// pause, never as a number ticking up: a tab in the background is woken up
// rarely, and the clock is the only thing that knows how long it slept. It is
// kept nowhere but in the open page: a reload or a closed tab drops it.

import type { ScheduleEntry } from "./types";

export const STEP_MIN = 5;
export const MIN_MIN = 1;
export const MAX_MIN = 600;

const MINUTE_MS = 60_000;

export interface TimerState {
  // The task it runs for, fixed once it has run: a new solve may move the
  // task's sessions, but the time already counted is still that task's.
  taskId: number;
  name: string;
  session: number;
  sessions: number;
  // What it is set to, and so the most it will log.
  minutes: number;
  // Counted before the run going on now, if any.
  countedMs: number;
  // When the run going on now began, or null while it is stopped.
  startedAt: number | null;
}

// A stopped timer, set to the length of a session the schedule planned.
export function timerFor(entry: ScheduleEntry): TimerState {
  const minutes = Math.round((Date.parse(entry.end) - Date.parse(entry.start)) / MINUTE_MS);
  return {
    taskId: Number(entry.id),
    name: entry.name,
    session: entry.session,
    sessions: entry.sessions,
    minutes: clampMinutes(minutes),
    countedMs: 0,
    startedAt: null,
  };
}

export const clampMinutes = (minutes: number): number =>
  Math.min(MAX_MIN, Math.max(MIN_MIN, Math.round(minutes) || MIN_MIN));

export const isRunning = (timer: TimerState): boolean => timer.startedAt !== null;

// Whether it has counted anything, which is what ties it to its task.
export const hasCounted = (timer: TimerState, now: number): boolean => counted(timer, now) > 0;

// How long it has counted, never more than it is set to.
export function counted(timer: TimerState, now: number): number {
  const running = timer.startedAt === null ? 0 : Math.max(0, now - timer.startedAt);
  return Math.min(timer.countedMs + running, timer.minutes * MINUTE_MS);
}

export const remaining = (timer: TimerState, now: number): number => timer.minutes * MINUTE_MS - counted(timer, now);

export const isFinished = (timer: TimerState, now: number): boolean => remaining(timer, now) <= 0;

// The whole minutes it would log: what it counted, to the nearest minute, and
// not more than it was set to.
export const minutesToLog = (timer: TimerState, now: number): number =>
  Math.min(timer.minutes, Math.round(counted(timer, now) / MINUTE_MS));

export function start(timer: TimerState, now: number): TimerState {
  return isRunning(timer) || isFinished(timer, now) ? timer : { ...timer, startedAt: now };
}

export function pause(timer: TimerState, now: number): TimerState {
  return isRunning(timer) ? { ...timer, countedMs: counted(timer, now), startedAt: null } : timer;
}

// A run that has reached what the timer is set to, stopped at that moment
// rather than whenever the page next looked; anything else as it is.
export function settle(timer: TimerState, now: number): TimerState {
  return isRunning(timer) && isFinished(timer, now)
    ? { ...timer, countedMs: timer.minutes * MINUTE_MS, startedAt: null }
    : timer;
}

// Setting it shorter than what it has counted ends the run there.
export function setMinutes(timer: TimerState, minutes: number, now: number): TimerState {
  const next = { ...timer, minutes: clampMinutes(minutes) };
  return isRunning(next) && isFinished(next, now) ? pause(next, now) : next;
}

// mm:ss, or h:mm:ss from an hour up.
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${String(minutes).padStart(2, "0")}:${seconds}`;
}
