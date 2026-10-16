import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "./api";
import { SkipDialog, SpendDialog } from "./dialogs";
import { Schedule } from "./Schedule";
import { Button, Table } from "./Table";
import { ROUTINES, TASKS, TIME_BLOCKS } from "./tables";
import { TimerPanel } from "./TimerPanel";
import type { Listing, Me, Routine, SolvedSchedule, Task } from "./types";

// What `/oauth/callback` sends back in `?error=` when signing in did not work.
export const LOGIN_ERRORS: Record<string, string> = {
  denied: "Sign-in was cancelled.",
  state: "That sign-in attempt has expired. Please try again.",
  discord: "Discord did not respond. Please try again a little later.",
  no_channel: "This account has no schedule channel. Please create one in Discord first with /schedule_channel create.",
};

const TABS = [
  { id: "tasks", label: "Tasks" },
  { id: "routines", label: "Routines" },
  { id: "time-blocks", label: "Time blocks" },
];

// The timer's panel: to the right of the calendar on a wide screen, above it on a
// narrow one (the stylesheet's breakpoint is the same), folded to a strip or not.
const STACKED_BELOW_PX = 760;
const PANEL_PX = 280;
const STRIP_PX = 36;
const PANEL_KEY = "olive.panel";

function panelOpenAtStart(): boolean {
  try {
    const kept = window.localStorage.getItem(PANEL_KEY);
    if (kept !== null) return kept === "open";
  } catch {
    // Nothing kept, then.
  }
  return window.innerWidth >= STACKED_BELOW_PX;
}

function keepPanelOpen(open: boolean) {
  try {
    window.localStorage.setItem(PANEL_KEY, open ? "open" : "closed");
  } catch {
    // Not kept: the next visit starts from the default again.
  }
}

// Three days to begin with if three columns of at least MIN_DAY_PX fit beside
// the panel, one otherwise; seven only when asked for.
const MIN_DAY_PX = 200;
function daysAtStart(panelOpen: boolean): number {
  const beside = window.innerWidth >= STACKED_BELOW_PX ? (panelOpen ? PANEL_PX : STRIP_PX) + 16 : 0;
  // The page's and the calendar card's padding, and the hours down its left.
  const room = window.innerWidth - beside - 32 - 26 - 44;
  return room / 3 >= MIN_DAY_PX ? 3 : 1;
}

function tabFromHash(): string {
  const hash = window.location.hash.slice(1);
  return TABS.some((tab) => tab.id === hash) ? hash : "tasks";
}

type View = { kind: "loading" } | { kind: "login"; error?: string } | { kind: "app"; me: Me };
type Status = { text: string; kind: "ok" | "error" };

export default function App({ loginError }: { loginError?: string }) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [status, setStatus] = useState<Status>({ text: "", kind: "ok" });
  const [tab, setTab] = useState(tabFromHash);
  const [completed, setCompleted] = useState<Task[]>([]);
  const [spending, setSpending] = useState<Task | null>(null);
  const [skipping, setSkipping] = useState<Routine | null>(null);
  const [tasksVersion, setTasksVersion] = useState(0);
  const [routinesVersion, setRoutinesVersion] = useState(0);
  const [scheduleVersion, setScheduleVersion] = useState(0);
  const [solved, setSolved] = useState<SolvedSchedule | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [panelOpen, setPanelOpen] = useState(panelOpenAtStart);
  const [daysShown] = useState(() => daysAtStart(panelOpen));

  const notify = useCallback((text: string) => setStatus({ text, kind: "ok" }), []);

  const fail = useCallback((error: unknown) => {
    const failure = error instanceof ApiError ? error : new ApiError(String(error));
    if (failure.status === 401) return setView({ kind: "login", error: "Your session has ended. Please sign in again." });
    if (failure.status === 403 && !failure.message.startsWith("Cross-origin")) {
      return setView({ kind: "login", error: LOGIN_ERRORS.no_channel });
    }
    setStatus({ text: failure.message, kind: "error" });
  }, []);

  const onTasksLoaded = useCallback((data: Listing) => {
    setTasks(data.tasks as Task[]);
    setCompleted(data.completed as Task[]);
  }, []);
  // Anything the page writes changes what the schedule above the tables shows.
  const dataChanged = useCallback(() => setScheduleVersion((version) => version + 1), []);

  // A message about something that went right fades; an error stays until the next one.
  useEffect(() => {
    if (status.kind !== "ok" || !status.text) return;
    const timer = window.setTimeout(() => setStatus({ text: "", kind: "ok" }), 5000);
    return () => window.clearTimeout(timer);
  }, [status]);

  useEffect(() => {
    let cancelled = false;
    api<Me>("GET", "/api/me").then(
      (me) => {
        if (!cancelled) setView({ kind: "app", me });
      },
      (error: ApiError) => {
        if (cancelled) return;
        if (error.status === 403) setView({ kind: "login", error: LOGIN_ERRORS.no_channel });
        else setView({ kind: "login", error: error.status === 401 ? loginError : error.message });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [loginError]);

  useEffect(() => {
    if (view.kind === "app") window.history.replaceState(null, "", `#${tab}`);
  }, [tab, view.kind]);

  async function logout() {
    try {
      await api("POST", "/logout");
    } finally {
      setView({ kind: "login" });
    }
  }

  function togglePanel() {
    keepPanelOpen(!panelOpen);
    setPanelOpen(!panelOpen);
  }

  // True once the minutes are logged; the timer only lets go of them then.
  async function spend(task: { id: number; name: string }, minutes: number): Promise<boolean> {
    try {
      const result = await api<{ completed: boolean; remaining_min: number }>("POST", `/api/tasks/${task.id}/spend`, {
        minutes,
      });
      setTasksVersion((version) => version + 1);
      dataChanged();
      notify(
        result.completed
          ? `“${task.name}” is done and moved to the history.`
          : `Logged ${minutes} min. ${result.remaining_min} min left.`,
      );
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  }

  async function skip(routine: Routine, resumeAfter: string) {
    try {
      await api("POST", `/api/routines/${routine.id}/skip`, { resume_after: resumeAfter });
      setRoutinesVersion((version) => version + 1);
      dataChanged();
      notify(`Routine #${routine.id} is skipped up to and including ${resumeAfter}.`);
    } catch (error) {
      fail(error);
    }
  }

  return (
    <>
      <header className="bar">
        <h1>Schedule</h1>
        {view.kind === "app" && (
          <div id="who" className="who">
            <span id="who-name">{view.me.name}</span>
            <button id="logout" type="button" className="link" onClick={() => void logout()}>
              Log out
            </button>
          </div>
        )}
      </header>

      <main>
        {view.kind === "loading" && (
          <p id="loading" className="muted">
            Loading…
          </p>
        )}

        {view.kind === "login" && (
          <section id="login-view" className="card">
            <p>
              Edit the tasks, routines and time blocks of your schedule as tables. Sign in with the Discord account you
              use with the bot.
            </p>
            <p id="login-error" className="error" hidden={!view.error}>
              {view.error}
            </p>
            <a className="button primary" href="/login">
              Sign in with Discord
            </a>
          </section>
        )}

        {view.kind === "app" && (
          <section id="app-view">
            <div className={panelOpen ? "workspace" : "workspace panel-closed"}>
              <Schedule version={scheduleVersion} fail={fail} initialSpan={daysShown} onSolved={setSolved} />
              <TimerPanel schedule={solved} tasks={tasks} open={panelOpen} onToggle={togglePanel} onLog={spend} />
            </div>

            <nav className="tabs" role="tablist">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  data-tab={item.id}
                  aria-selected={tab === item.id}
                  onClick={() => setTab(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </nav>
            <p id="status" className={`status ${status.kind}`} role="status" aria-live="polite">
              {status.text}
            </p>

            <div id="tab-tasks" role="tabpanel" hidden={tab !== "tasks"}>
              <p className="muted hint">
                The schedule above and the one in Discord are recalculated as soon as you save. Duration is the time
                still left; leave Session empty to keep a task in one piece, and Min. empty to let the bot choose.
              </p>
              <Table
                config={TASKS}
                notify={notify}
                fail={fail}
                onLoaded={onTasksLoaded}
                onChange={dataChanged}
                version={tasksVersion}
                renewOnVersion
                actions={(task) => <Button text="⏱" title="Log time spent" onClick={() => setSpending(task)} />}
              />
              <details id="history" className="card">
                <summary>
                  Completed (<span id="history-count">{completed.length}</span>)
                </summary>
                <ul id="history-list">
                  {completed.map((task) => (
                    <li key={task.id}>
                      #{task.id} {task.name}
                    </li>
                  ))}
                </ul>
              </details>
            </div>

            <div id="tab-routines" role="tabpanel" hidden={tab !== "routines"}>
              <p className="muted hint">
                A fixed routine takes place at a set time; a flexible one goes anywhere before its deadline. Skipping
                leaves a routine out of the schedule up to and including the date you pick.
              </p>
              <Table
                config={ROUTINES}
                notify={notify}
                fail={fail}
                onChange={dataChanged}
                version={routinesVersion}
                actions={(routine) => <Button text="⏸" title="Skip" onClick={() => setSkipping(routine)} />}
              />
            </div>

            <div id="tab-time-blocks" role="tabpanel" hidden={tab !== "time-blocks"}>
              <p className="muted hint">
                Time you are busy: nothing is scheduled into it. Daily and weekly blocks only use the time of day.
              </p>
              <Table config={TIME_BLOCKS} notify={notify} fail={fail} onChange={dataChanged} />
            </div>
          </section>
        )}
      </main>

      <SpendDialog
        task={spending}
        onDone={(minutes) => {
          if (spending && minutes !== null) void spend(spending, minutes);
          setSpending(null);
        }}
      />
      <SkipDialog
        routine={skipping}
        onDone={(resumeAfter) => {
          if (skipping && resumeAfter !== null) void skip(skipping, resumeAfter);
          setSkipping(null);
        }}
      />
    </>
  );
}
