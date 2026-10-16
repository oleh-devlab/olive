import type { ReactNode } from "react";

import { ApiError } from "./api";
import { WEEKDAYS, describeRepeat, displayDateTime, isPast, localToday, timeOf, wallClock } from "./dates";
import type { Defaults, Item, Limits, Task, TimeBlock } from "./types";

// While a row is being edited it is a draft: the text of each input, by key.
export type Draft = Record<string, string | number[]>;
export type Row = Record<string, unknown>;

export interface Context {
  items: Item[];
  limits: Limits;
  defaults: Defaults;
}

export type Shown = string | number | { text: string | number; className?: string; title?: string };

// Each column says how an item shows in it (`view`), what it keeps of the item
// while it is edited (`init`), what edits that (`edit`, focused when a row has
// just opened and the column is its first), and how the edited value goes into
// the row sent to the API (`read`). A column may read keys another column
// keeps, the way a block's bounds follow its repeat.
export interface Column<T> {
  label: string;
  title?: string;
  numeric?: boolean;
  className?: string;
  view: (item: T, context: Context) => Shown;
  init: (item: T | null, context: Context) => Draft;
  edit: (draft: Draft, set: (patch: Draft) => void, context: Context, autoFocus: boolean) => ReactNode;
  read: (draft: Draft, row: Row) => void;
}

export const text = (draft: Draft, key: string): string => (draft[key] as string | undefined) ?? "";

function readNumber(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  if (!Number.isInteger(n)) throw new ApiError(`“${label}” must be a whole number.`);
  return n;
}

function nameOf(items: Item[], id: number): string {
  return items.find((item) => item.id === id)?.name ?? "(no such item)";
}

export function nameColumn<T extends Item>(placeholder: string, required = true): Column<T> {
  return {
    label: "Name",
    className: "name",
    view: (item) => item.name || (required ? "" : "—"),
    init: (item) => ({ name: item?.name ?? "" }),
    edit: (draft, set, context, autoFocus) => (
      <input
        autoFocus={autoFocus}
        type="text"
        className="in-name"
        maxLength={context.limits.name_length || 100}
        placeholder={placeholder}
        value={text(draft, "name")}
        onChange={(e) => set({ name: e.target.value })}
      />
    ),
    read: (draft, row) => {
      row.name = text(draft, "name").trim();
    },
  };
}

interface NumberOptions {
  min?: number;
  max?: number;
  blank?: string;
  title?: string;
}

export function numberColumn<T>(key: keyof T & string, label: string, options: NumberOptions = {}): Column<T> {
  const { min, max, blank, title } = options;
  return {
    label,
    title,
    numeric: true,
    view: (item) => (item[key] as number | null) ?? blank ?? "—",
    init: (item, context) => {
      const value = item ? (item[key] as number | null) : context.defaults[key];
      return { [key]: value === null || value === undefined ? "" : String(value) };
    },
    edit: (draft, set, _context, autoFocus) => (
      <input
        autoFocus={autoFocus}
        type="number"
        min={min}
        max={max}
        step={1}
        inputMode="numeric"
        placeholder={blank}
        value={text(draft, key)}
        onChange={(e) => set({ [key]: e.target.value })}
      />
    ),
    read: (draft, row) => {
      row[key] = readNumber(text(draft, key), label);
    },
  };
}

export function textColumn<T>(key: keyof T & string, label: string, lengthLimit: keyof Limits): Column<T> {
  return {
    label,
    className: key,
    view: (item) => ({ text: item[key] as string, title: item[key] as string }),
    init: (item) => ({ [key]: item ? (item[key] as string) : "" }),
    edit: (draft, set, context, autoFocus) => (
      <input
        autoFocus={autoFocus}
        type="text"
        className={`in-${key}`}
        maxLength={context.limits[lengthLimit] || 1000}
        value={text(draft, key)}
        onChange={(e) => set({ [key]: e.target.value })}
      />
    ),
    read: (draft, row) => {
      row[key] = text(draft, key).trim();
    },
  };
}

export function idsColumn<T extends { depends_on: number[] }>(label: string, title: string): Column<T> {
  return {
    label,
    title,
    view: (item, context) => ({
      text: item.depends_on.map((id) => `#${id}`).join(", ") || "—",
      title: item.depends_on.map((id) => `#${id} ${nameOf(context.items, id)}`).join("\n"),
    }),
    init: (item) => ({ depends_on: (item?.depends_on ?? []).join(", ") }),
    edit: (draft, set, _context, autoFocus) => (
      <input
        autoFocus={autoFocus}
        type="text"
        className="in-ids"
        placeholder="1, 3"
        value={text(draft, "depends_on")}
        onChange={(e) => set({ depends_on: e.target.value })}
      />
    ),
    read: (draft, row) => {
      const ids = text(draft, "depends_on").trim().split(/[\s,;#]+/).filter(Boolean).map(Number);
      if (ids.some((n) => !Number.isInteger(n))) throw new ApiError(`“${label}” takes IDs separated by commas.`);
      row.depends_on = ids;
    },
  };
}

export function deadlineColumn(): Column<Task> {
  return {
    label: "Deadline",
    view: (item) =>
      isPast(item.deadline)
        ? { text: displayDateTime(item.deadline), className: "overdue", title: "The deadline has passed" }
        : displayDateTime(item.deadline),
    init: (item) => ({ deadline: wallClock(item?.deadline) }),
    edit: (draft, set, _context, autoFocus) => (
      <input
        autoFocus={autoFocus}
        type="datetime-local"
        className="in-datetime"
        value={text(draft, "deadline")}
        onChange={(e) => set({ deadline: e.target.value })}
      />
    ),
    read: (draft, row) => {
      row.deadline = text(draft, "deadline") || null;
    },
  };
}

export function repeatColumn<T extends { repeat: string; weekdays?: number[] | null }>(
  options: [string, string][],
): Column<T> {
  return {
    label: "Repeat",
    view: (item) => describeRepeat(item.repeat, item.weekdays),
    init: (item) => ({ repeat: item?.repeat ?? options[0][0], weekdays: item?.weekdays ?? [] }),
    edit: (draft, set, _context, autoFocus) => {
      const repeat = text(draft, "repeat");
      const days = (draft.weekdays as number[] | undefined) ?? [];
      const toggle = (day: number, on: boolean) =>
        set({ weekdays: on ? [...days, day].sort((a, b) => a - b) : days.filter((d) => d !== day) });
      return (
        <span className="repeat">
          <select autoFocus={autoFocus} value={repeat} onChange={(e) => set({ repeat: e.target.value })}>
            {options.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <span className="weekdays" hidden={repeat !== "weekly"}>
            {WEEKDAYS.map((day, index) => (
              <label key={day}>
                <input
                  type="checkbox"
                  value={index}
                  checked={days.includes(index)}
                  onChange={(e) => toggle(index, e.target.checked)}
                />
                {day.slice(0, 2)}
              </label>
            ))}
          </span>
        </span>
      );
    },
    read: (draft, row) => {
      row.repeat = text(draft, "repeat");
      row.weekdays = row.repeat === "weekly" ? ((draft.weekdays as number[] | undefined) ?? []) : null;
    },
  };
}

// One end of a time block: a date and time when it happens once, a time of day
// when it repeats. A repeating block's date is only a template, so both ends of
// it are sent on the same one -- the block's own start date, or today for a new
// block -- and the server moves an end before the start onto the next day.
export function boundColumn(key: "start" | "end", label: string): Column<TimeBlock> {
  const dateKey = `${key}Date`;
  const timeKey = `${key}Time`;
  return {
    label,
    view: (item) => (item.repeat === "once" ? displayDateTime(item[key]) : timeOf(item[key])),
    init: (item) => ({
      [dateKey]: item ? item[key].slice(0, 10) : localToday(),
      [timeKey]: timeOf(item?.[key]),
      anchor: item ? item.start.slice(0, 10) : localToday(),
    }),
    edit: (draft, set, _context, autoFocus) => {
      const date = text(draft, dateKey);
      const time = text(draft, timeKey);
      if (text(draft, "repeat") === "once") {
        return (
          <input
            key="once"
            autoFocus={autoFocus}
            type="datetime-local"
            className="in-datetime"
            value={time ? `${date}T${time}` : ""}
            onChange={(e) => {
              const value = e.target.value;
              set(value ? { [dateKey]: value.slice(0, 10), [timeKey]: value.slice(11, 16) } : { [timeKey]: "" });
            }}
          />
        );
      }
      return (
        <input
          key="repeating"
          autoFocus={autoFocus}
          type="time"
          className="in-datetime"
          value={time}
          onChange={(e) => set({ [timeKey]: e.target.value })}
        />
      );
    },
    read: (draft, row) => {
      const time = text(draft, timeKey);
      const date = text(draft, "repeat") === "once" ? text(draft, dateKey) : text(draft, "anchor");
      row[key] = time ? `${date}T${time}` : null;
    },
  };
}
