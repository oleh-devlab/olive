import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError, api } from "./api";
import { SkipDialog, SpendDialog } from "./dialogs";
import { Schedule, planFacts, solveFacts } from "./Schedule";
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

// The timer's panel: beside the calendar on a wide screen, above it on a
// narrow one (the stylesheet's breakpoint is the same), or folded away into
// the calendar's toolbar, where it takes no width of its own.
const STACKED_BELOW_PX = 760;
const PANEL_PX = 248;
// The only three things the page keeps in the browser: whether the panel was
// left folded, how many days the calendar was last set to show, and whether
// it was told not to scroll inside itself.
const PANEL_KEY = "olive.panel";
const DAYS_KEY = "olive.days";
const SCROLL_KEY = "olive.scroll";
const DAY_CHOICES = [1, 3, 7];

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

function keepDays(days: number) {
  try {
    window.localStorage.setItem(DAYS_KEY, String(days));
  } catch {
    // Not kept: the next visit works the number out again.
  }
}

// Whether a calendar much taller than the screen scrolls inside itself, as it
// does until it is told not to.
function scrollAtStart(): boolean {
  try {
    return window.localStorage.getItem(SCROLL_KEY) !== "off";
  } catch {
    return true;
  }
}

function keepScroll(on: boolean) {
  try {
    window.localStorage.setItem(SCROLL_KEY, on ? "on" : "off");
  } catch {
    // Not kept: the next visit scrolls inside again.
  }
}

// The number of days last chosen; else three if three columns of at least
// MIN_DAY_PX fit beside the panel, one otherwise.
const MIN_DAY_PX = 200;
function daysAtStart(panelOpen: boolean): number {
  try {
    const kept = Number(window.localStorage.getItem(DAYS_KEY));
    if (DAY_CHOICES.includes(kept)) return kept;
  } catch {
    // Nothing kept, then.
  }
  const beside = window.innerWidth >= STACKED_BELOW_PX && panelOpen ? PANEL_PX + 6 : 0;
  // The page's and the calendar card's padding, and the hours down its left.
  const room = window.innerWidth - beside - 16 - 18 - 36;
  return room / 3 >= MIN_DAY_PX ? 3 : 1;
}

// How the header holds what the Discord message says above the schedule:
// all on its one line, between the title and the user's name ("line"); with
// how far ahead it plans moved under the name ("stacked"); or, when not even
// that fits, as on a phone, when it was solved on a line of its own below
// ("below"). Each is tried in that order, from the widths of the parts --
// which do not change with where the text goes, so the choice holds still.
type HeaderLayout = "line" | "stacked" | "below";
const HEADER_GAP_PX = 12;

let measurer: CanvasRenderingContext2D | null = null;

function textWidth(text: string, like: Element): number {
  if (!text) return 0;
  measurer ??= document.createElement("canvas").getContext("2d");
  if (!measurer) return 0;
  const style = getComputedStyle(like);
  measurer.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  return measurer.measureText(text).width;
}

function layOutHeader(header: HTMLElement, solved: string, plan: string): HeaderLayout {
  const facts = header.querySelector(".head-facts");
  const title = header.querySelector("h1");
  const who = header.querySelector(".who-line");
  if (!facts || !title || !who) return "line";
  const style = getComputedStyle(header);
  const room = header.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const titleWidth = title.getBoundingClientRect().width;
  const whoWidth = who.getBoundingClientRect().width;
  const solvedWidth = textWidth(solved, facts);
  const planWidth = textWidth(plan, facts);
  const separator = plan && solved ? textWidth(SEPARATOR, facts) : 0;
  // A pixel each way of what a canvas and the page may round differently.
  const fits = (...widths: number[]) =>
    widths.reduce((sum, width) => sum + width, 0) + HEADER_GAP_PX * (widths.length - 1) + 2 <= room;

  if (fits(titleWidth, solvedWidth + separator + planWidth, whoWidth)) return "line";
  if (fits(titleWidth, solvedWidth, Math.max(whoWidth, planWidth))) return "stacked";
  return "below";
}

const SEPARATOR = " · ";

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
  const solvedFacts = solved ? solveFacts(solved) : "";
  const plan = solved ? planFacts(solved) : "";
  const header = useRef<HTMLElement>(null);
  const [headerLayout, setHeaderLayout] = useState<HeaderLayout>("line");
  useLayoutEffect(() => {
    const box = header.current;
    if (!box) return;
    const decide = () => setHeaderLayout(layOutHeader(box, solvedFacts, plan));
    decide();
    const observer = new ResizeObserver(decide);
    observer.observe(box);
    return () => observer.disconnect();
  }, [solvedFacts, plan, view.kind]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [panelOpen, setPanelOpen] = useState(panelOpenAtStart);
  const [daysShown] = useState(() => daysAtStart(panelOpen));
  // Where the folded timer goes: a place in the calendar's toolbar.
  const [timerSlot, setTimerSlot] = useState<HTMLElement | null>(null);

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
      {/* What the Discord message says above the schedule goes here rather
          than above the calendar, which keeps its toolbar to its controls. */}
      <header className={`bar header-${headerLayout}`} ref={header}>
        <h1>Schedule</h1>
        {view.kind === "app" && (
          <>
            <p className="head-facts muted">
              {solvedFacts}
              {headerLayout === "line" && plan && `${solvedFacts ? SEPARATOR : ""}${plan}`}
            </p>
            <div id="who" className="who">
              <div className="who-line">
                <span id="who-name">{view.me.name}</span>
                <button id="logout" type="button" className="link" onClick={() => void logout()}>
                  Log out
                </button>
              </div>
              {headerLayout !== "line" && plan && <span className="head-plan muted">{plan}</span>}
            </div>
          </>
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
              <TimerPanel
                schedule={solved}
                tasks={tasks}
                open={panelOpen}
                onToggle={togglePanel}
                onLog={spend}
                foldedInto={timerSlot}
              />
              <Schedule
                toolbarSlot={setTimerSlot}
                version={scheduleVersion}
                fail={fail}
                initialSpan={daysShown}
                onSpanChange={keepDays}
                initialScroll={scrollAtStart()}
                onScrollChange={keepScroll}
                onSolved={setSolved}
                tasks={tasks}
              />
            </div>

            {/* The status shares the tabs' row rather than taking one of its own. */}
            <div className="tabs-bar">
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
            </div>

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
              <details id="history">
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
