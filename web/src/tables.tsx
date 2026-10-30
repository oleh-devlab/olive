import {
  boundColumn,
  deadlineColumn,
  idsColumn,
  nameColumn,
  numberColumn,
  repeatColumn,
  text,
  textColumn,
  type Column,
} from "./columns";
import { skippedUntil } from "./dates";
import type { TableConfig } from "./Table";
import type { Routine, Task, TimeBlock } from "./types";

const PRIORITY_TITLE = "Priority: 0 — anywhere; 1–10 — the higher, the earlier";
// Under the tables whose priority column is headed only "P.".
const PRIORITY_NOTE = "P. — priority: 0 places it anywhere; 1–10, the higher, the earlier.";

export const TASKS: TableConfig<Task> = {
  id: "tasks",
  endpoint: "/api/tasks",
  listKey: "tasks",
  noun: "Task",
  plural: "tasks",
  empty: "No tasks yet. Add the first one in the row above.",
  note: PRIORITY_NOTE,
  columns: [
    nameColumn("New task"),
    numberColumn("duration", "Duration", { min: 1, title: "Minutes still left" }),
    numberColumn("priority", "P.", { min: 0, max: 10, title: PRIORITY_TITLE }),
    deadlineColumn(),
    numberColumn("max_chunk_duration", "Session", { min: 1, blank: "whole", title: "Longest session, min" }),
    numberColumn("min_chunk_duration", "Min.", { min: 0, blank: "auto", title: "Shortest session, min" }),
    numberColumn("break_duration", "Break", { min: 0, title: "Break after each session, min" }),
    idsColumn("After", "IDs of the tasks this one comes after"),
    textColumn("description", "Description", "description_length", "Desc."),
  ],
};

const routineType: Column<Routine> = {
  label: "Type",
  title: "Fixed: at a set time. Flexible: anywhere before its deadline.",
  view: (item) => (item.type === "fixed" ? "Fixed" : "Flexible"),
  init: (item) => ({ type: item?.type ?? "fixed" }),
  edit: (draft, set, _context, autoFocus) => (
    <select autoFocus={autoFocus} value={text(draft, "type")} onChange={(e) => set({ type: e.target.value })}>
      <option value="fixed">Fixed</option>
      <option value="flexible">Flexible</option>
    </select>
  ),
  read: (draft, row) => {
    row.type = text(draft, "type");
  },
};

// A fixed routine's start and a flexible one's deadline share a column: which
// of the two it is follows the type.
const routineTime: Column<Routine> = {
  label: "Time",
  title: "Fixed: when it starts. Flexible: when it has to be done by.",
  view: (item) => (item.type === "fixed" ? `at ${item.time ?? "—"}` : `by ${item.deadline_time ?? "—"}`),
  init: (item) => ({ clock: (item && (item.type === "fixed" ? item.time : item.deadline_time)) ?? "" }),
  edit: (draft, set, _context, autoFocus) => (
    <input autoFocus={autoFocus} type="time" value={text(draft, "clock")} onChange={(e) => set({ clock: e.target.value })} />
  ),
  read: (draft, row) => {
    const value = text(draft, "clock") || null;
    row.time = text(draft, "type") === "fixed" ? value : null;
    row.deadline_time = text(draft, "type") === "flexible" ? value : null;
  },
};

// Shown, not edited: a skip has an endpoint of its own, and saving a row leaves it as it is.
const routineSkip: Column<Routine> = {
  label: "Skipped until",
  title: "Left out of the schedule up to and including this date",
  view: (item) => skippedUntil(item),
  init: (item) => ({ skipped: item ? skippedUntil(item) : "" }),
  edit: (draft) => text(draft, "skipped"),
  read: () => {},
};

export const ROUTINES: TableConfig<Routine> = {
  id: "routines",
  endpoint: "/api/routines",
  listKey: "routines",
  noun: "Routine",
  plural: "routines",
  empty: "No routines yet. Add the first one in the row above.",
  note: PRIORITY_NOTE,
  columns: [
    nameColumn("New routine"),
    routineType,
    routineTime,
    numberColumn("duration", "Duration", { min: 1, title: "Minutes" }),
    numberColumn("priority", "P.", { min: 0, max: 10, title: PRIORITY_TITLE }),
    numberColumn("break_duration", "Break", { min: 0, title: "Break after it, min" }),
    repeatColumn([
      ["daily", "Daily"],
      ["weekly", "Weekly"],
    ]),
    idsColumn("After", "IDs of the routines this one comes after"),
    routineSkip,
  ],
};

export const TIME_BLOCKS: TableConfig<TimeBlock> = {
  id: "time-blocks",
  endpoint: "/api/time-blocks",
  listKey: "time_blocks",
  noun: "Time block",
  plural: "time blocks",
  empty: "No time blocks yet. Add the first one in the row above.",
  columns: [
    nameColumn("(optional)", false),
    repeatColumn([
      ["once", "Once"],
      ["daily", "Daily"],
      ["weekly", "Weekly"],
    ]),
    boundColumn("start", "Start"),
    boundColumn("end", "End"),
  ],
};
