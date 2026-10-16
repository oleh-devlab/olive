"use strict";

// What `/oauth/callback` sends back in `?error=` when signing in did not work.
const LOGIN_ERRORS = {
  denied: "Sign-in was cancelled.",
  state: "That sign-in attempt has expired. Please try again.",
  discord: "Discord did not respond. Please try again a little later.",
  no_channel: "This account has no schedule channel. Please create one in Discord first with /schedule_channel create.",
};

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const byId = (id) => document.getElementById(id);

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(method, path, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError("The server is not responding.", 0);
  }

  if (response.status === 204) return null;

  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || `Error ${response.status}`, response.status);
  return data;
}

// --- Views and messages ---

function show(view) {
  for (const id of ["loading", "login-view", "app-view"]) byId(id).hidden = id !== view;
}

function showLogin(errorText) {
  show("login-view");
  byId("who").hidden = true;
  const error = byId("login-error");
  error.textContent = errorText || "";
  error.hidden = !errorText;
}

let statusTimer = null;

function setStatus(text, kind = "ok") {
  const status = byId("status");
  status.textContent = text;
  status.className = `status ${kind}`;
  clearTimeout(statusTimer);
  if (kind === "ok") statusTimer = setTimeout(() => (status.textContent = ""), 5000);
}

function fail(error) {
  if (error.status === 401) return showLogin("Your session has ended. Please sign in again.");
  if (error.status === 403 && !(error.message || "").startsWith("Cross-origin")) {
    return showLogin(LOGIN_ERRORS.no_channel);
  }
  setStatus(error.message, "error");
}

// --- Dates and times ---
// A deadline and a block's bounds come as ISO with the bot's own offset
// ("2026-10-25T18:00:00+02:00"). Their first 16 characters are the bot's
// wall-clock time, which is what a datetime input holds and what the API takes
// back. A routine's times are "HH:MM" and its skip date "DD.MM.YYYY".

const wallClock = (iso) => (iso ? iso.slice(0, 16) : "");
const timeOf = (iso) => (iso ? iso.slice(11, 16) : "");

function displayDateTime(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : "—";
}

function isPast(iso) {
  return Boolean(iso) && new Date(iso) < new Date();
}

function localToday() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// "DD.MM.YYYY" as stored, and the "YYYY-MM-DD" a date input holds.
function isoDate(stored) {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(stored || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}

function storedDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  return m ? `${m[3]}.${m[2]}.${m[1]}` : null;
}

function describeRepeat(repeat, weekdays) {
  if (repeat === "weekly") return (weekdays || []).map((d) => WEEKDAYS[d]).join(", ");
  return repeat === "once" ? "Once" : "Daily";
}

// A skip whose date has passed no longer leaves anything out.
function skippedUntil(routine) {
  const until = isoDate(routine.resume_after);
  return until && until >= localToday() ? routine.resume_after : "—";
}

// --- Small DOM helpers ---

function cell(text, className, title) {
  const td = document.createElement("td");
  td.textContent = text === null || text === undefined ? "" : String(text);
  if (className) td.className = className;
  if (title) td.title = title;
  return td;
}

function button(text, title, onClick, className = "") {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  b.title = title;
  b.setAttribute("aria-label", title);
  if (className) b.className = className;
  b.addEventListener("click", onClick);
  return b;
}

function input(type, value, properties = {}) {
  const el = document.createElement("input");
  el.type = type;
  el.value = value ?? "";
  for (const [key, property] of Object.entries(properties)) {
    if (property !== undefined && property !== null) el[key] = property;
  }
  return el;
}

function select(options, value) {
  const el = document.createElement("select");
  for (const [optionValue, label] of options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = label;
    el.append(option);
  }
  el.value = value ?? options[0][0];
  return el;
}

function weekdayPicker(selected) {
  const box = document.createElement("span");
  box.className = "weekdays";
  WEEKDAYS.forEach((day, index) => {
    const label = document.createElement("label");
    const check = input("checkbox", String(index), { checked: (selected || []).includes(index) });
    label.append(check, day.slice(0, 2));
    box.append(label);
  });
  return box;
}

function readNumber(el, label) {
  const value = el.value.trim();
  if (value === "") return null;
  const n = Number(value);
  if (!Number.isInteger(n)) throw new ApiError(`“${label}” must be a whole number.`);
  return n;
}

// --- Columns ---
// Each column says how an item shows in it (`view`), what edits it (`edit`),
// and how the edited value goes into the row sent to the API (`read`). `bind`
// runs once the whole editing row exists, for columns that follow another one.

const column = {
  name: (placeholder, required = true) => ({
    label: "Name",
    className: "name",
    view: (item) => item.name || (required ? "" : "—"),
    edit: (item, table) =>
      input("text", item?.name, { maxLength: table.limits.name_length || 100, placeholder, className: "in-name" }),
    read: (td, row) => {
      row.name = td.firstChild.value.trim();
    },
  }),

  number: (key, label, { min, max, blank, title } = {}) => ({
    label,
    title,
    numeric: true,
    view: (item) => item[key] ?? blank ?? "—",
    edit: (item, table) =>
      input("number", item ? item[key] : table.defaults[key], { min, max, step: 1, inputMode: "numeric", placeholder: blank }),
    read: (td, row) => {
      row[key] = readNumber(td.firstChild, label);
    },
  }),

  text: (key, label, lengthLimit) => ({
    label,
    className: key,
    view: (item) => ({ text: item[key], title: item[key] }),
    edit: (item, table) => input("text", item?.[key], { maxLength: table.limits[lengthLimit] || 1000, className: `in-${key}` }),
    read: (td, row) => {
      row[key] = td.firstChild.value.trim();
    },
  }),

  ids: (label, title) => ({
    label,
    title,
    view: (item, table) => ({
      text: item.depends_on.map((id) => `#${id}`).join(", ") || "—",
      title: item.depends_on.map((id) => `#${id} ${table.nameOf(id)}`).join("\n"),
    }),
    edit: (item) => input("text", (item?.depends_on || []).join(", "), { placeholder: "1, 3", className: "in-ids" }),
    read: (td, row) => {
      const ids = td.firstChild.value.trim().split(/[\s,;#]+/).filter(Boolean).map(Number);
      if (ids.some((n) => !Number.isInteger(n))) throw new ApiError(`“${label}” takes IDs separated by commas.`);
      row.depends_on = ids;
    },
  }),

  deadline: () => ({
    label: "Deadline",
    view: (item) =>
      isPast(item.deadline)
        ? { text: displayDateTime(item.deadline), className: "overdue", title: "The deadline has passed" }
        : displayDateTime(item.deadline),
    edit: (item) => input("datetime-local", wallClock(item?.deadline), { className: "in-datetime" }),
    read: (td, row) => {
      row.deadline = td.firstChild.value || null;
    },
  }),

  repeat: (options) => ({
    label: "Repeat",
    view: (item) => describeRepeat(item.repeat, item.weekdays),
    edit: (item) => {
      const box = document.createElement("span");
      box.className = "repeat";
      const choice = select(options, item?.repeat);
      choice.dataset.role = "repeat";
      const days = weekdayPicker(item?.weekdays);
      const sync = () => (days.hidden = choice.value !== "weekly");
      choice.addEventListener("change", sync);
      sync();
      box.append(choice, days);
      return box;
    },
    read: (td, row) => {
      row.repeat = td.querySelector("select").value;
      row.weekdays = row.repeat === "weekly" ? [...td.querySelectorAll("input:checked")].map((c) => Number(c.value)) : null;
    },
  }),

  // One end of a time block: a date and time when it happens once, a time of
  // day when it repeats. A repeating block's date is only a template, so both
  // ends of it are sent on the same one -- the block's own, or today for a new
  // block -- and the server moves an end before the start onto the next day.
  bound: (key, label) => ({
    label,
    view: (item) => (item.repeat === "once" ? displayDateTime(item[key]) : timeOf(item[key])),
    edit: (item) => {
      const once = (item?.repeat ?? "once") === "once";
      const el = input(once ? "datetime-local" : "time", once ? wallClock(item?.[key]) : timeOf(item?.[key]), {
        className: "in-datetime",
      });
      el.dataset.bound = key;
      return el;
    },
    bind: (tr, item) => {
      tr.dataset.anchor = item?.start ? item.start.slice(0, 10) : localToday();
      const choice = tr.querySelector('select[data-role="repeat"]');
      const el = tr.querySelector(`[data-bound="${key}"]`);
      choice.addEventListener("change", () => {
        const once = choice.value === "once";
        if (once === (el.type === "datetime-local")) return;
        const value = el.value;
        el.type = once ? "datetime-local" : "time";
        el.value = once ? (value ? `${tr.dataset.anchor}T${value}` : "") : value.slice(11, 16);
      });
    },
    read: (td, row, tr) => {
      const el = td.firstChild;
      if (!el.value) row[key] = null;
      else row[key] = el.type === "time" ? `${tr.dataset.anchor}T${el.value}` : el.value;
    },
  }),
};

const ROUTINE_TYPES = [
  ["fixed", "Fixed"],
  ["flexible", "Flexible"],
];

// --- A table of one kind of item ---

class Table {
  constructor({ id, endpoint, listKey, noun, plural, fields, actions = () => [], afterLoad = () => {} }) {
    Object.assign(this, { id, endpoint, listKey, noun, plural, fields, actions, afterLoad });
    this.items = [];
    this.limits = {};
    this.defaults = {};
    this.editingId = null;
    this.busy = false;
  }

  mount() {
    const head = document.createElement("tr");
    const heading = (text, className, title) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = text;
      if (className) th.className = className;
      if (title) th.title = title;
      return th;
    };
    head.append(heading("ID", "num"));
    for (const field of this.fields) head.append(heading(field.label, field.numeric ? "num" : "", field.title));
    const actions = heading("");
    const hidden = document.createElement("span");
    hidden.className = "sr-only";
    hidden.textContent = "Actions";
    actions.append(hidden);
    head.append(actions);

    const thead = document.createElement("thead");
    thead.append(head);
    this.body = document.createElement("tbody");
    this.foot = document.createElement("tfoot");
    byId(`table-${this.id}`).replaceChildren(thead, this.body, this.foot);
  }

  async reload() {
    const data = await api("GET", this.endpoint);
    this.items = data[this.listKey];
    this.limits = data.limits || {};
    this.defaults = data.defaults || {};
    if (!this.items.some((item) => item.id === this.editingId)) this.editingId = null;
    this.renderRows();
    this.afterLoad(data);
  }

  nameOf(id) {
    const item = this.items.find((i) => i.id === id);
    return item ? item.name : "(no such item)";
  }

  renderRows() {
    const rows = this.items.map((item) => (item.id === this.editingId ? this.editorRow(item) : this.viewRow(item)));
    this.body.replaceChildren(...rows);
    byId(`tab-${this.id}`).querySelector(".empty").hidden = this.items.length > 0;
  }

  // Kept apart from the rest so that what is being typed into it survives
  // another row being edited or a reload.
  renderNewRow() {
    if (this.items.length >= (this.limits.max_items || Infinity)) {
      const tr = document.createElement("tr");
      const td = cell(`You have reached the limit of ${this.limits.max_items} ${this.plural}.`, "muted");
      td.colSpan = this.fields.length + 2;
      tr.append(td);
      this.foot.replaceChildren(tr);
    } else {
      this.foot.replaceChildren(this.editorRow(null));
    }
  }

  viewRow(item) {
    const tr = document.createElement("tr");
    tr.append(cell(item.id, "num id"));
    for (const field of this.fields) {
      const shown = field.view(item, this);
      const { text, className = "", title = "" } = typeof shown === "object" && shown !== null ? shown : { text: shown };
      const classes = [field.numeric ? "num" : "", field.className || "", className].filter(Boolean).join(" ");
      tr.append(cell(text, classes, title));
    }

    const actions = document.createElement("td");
    actions.className = "actions";
    actions.append(
      button("✎", "Edit", () => this.startEdit(item.id)),
      ...this.actions(item),
      button("✕", "Delete", () => this.remove(item), "danger"),
    );
    tr.append(actions);

    tr.addEventListener("dblclick", (event) => {
      if (!event.target.closest("button")) this.startEdit(item.id);
    });
    return tr;
  }

  editorRow(item) {
    const isNew = item === null;
    const tr = document.createElement("tr");
    tr.className = isNew ? "new" : "editing";
    tr.append(cell(isNew ? "+" : item.id, "num id"));

    tr.fieldCells = this.fields.map((field) => {
      const td = document.createElement("td");
      td.append(field.edit(item, this));
      tr.append(td);
      return td;
    });
    for (const field of this.fields) field.bind?.(tr, item);

    const save = () => this.save(tr, isNew ? null : item.id);
    const actions = document.createElement("td");
    actions.className = "actions";
    if (isNew) {
      actions.append(button("Add", `Add ${this.noun.toLowerCase()}`, save, "primary"));
    } else {
      actions.append(button("✓", "Save", save, "primary"), button("↺", "Cancel", () => this.cancelEdit()));
    }
    tr.append(actions);

    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.tagName === "INPUT") {
        event.preventDefault();
        save();
      } else if (event.key === "Escape" && !isNew) {
        this.cancelEdit();
      }
    });
    return tr;
  }

  readRow(tr) {
    const row = {};
    this.fields.forEach((field, index) => field.read(tr.fieldCells[index], row, tr));
    return row;
  }

  startEdit(id) {
    this.editingId = id;
    this.renderRows();
    this.body.querySelector("tr.editing input, tr.editing select")?.focus();
  }

  cancelEdit() {
    this.editingId = null;
    this.renderRows();
  }

  async save(tr, id) {
    if (this.busy) return;

    let row;
    try {
      row = this.readRow(tr);
    } catch (error) {
      return fail(error);
    }

    this.busy = true;
    tr.classList.add("saving");
    try {
      if (id === null) {
        const created = await api("POST", this.endpoint, row);
        await this.reload();
        this.renderNewRow();
        setStatus(`${this.noun} #${created.id} added.`);
        this.foot.querySelector("input")?.focus();
      } else {
        await api("PUT", `${this.endpoint}/${id}`, row);
        this.editingId = null;
        await this.reload();
        setStatus(`${this.noun} #${id} saved.`);
      }
    } catch (error) {
      fail(error);
    } finally {
      this.busy = false;
      tr.classList.remove("saving");
    }
  }

  async remove(item) {
    const label = item.name ? ` “${item.name}”` : "";
    if (!window.confirm(`Delete ${this.noun.toLowerCase()} #${item.id}${label}?`)) return;
    try {
      await api("DELETE", `${this.endpoint}/${item.id}`);
      await this.reload();
      this.renderNewRow();
      setStatus(`${this.noun} #${item.id} deleted.`);
    } catch (error) {
      fail(error);
    }
  }
}

// --- The three tables ---

const tasks = new Table({
  id: "tasks",
  endpoint: "/api/tasks",
  listKey: "tasks",
  noun: "Task",
  plural: "tasks",
  fields: [
    column.name("New task"),
    column.number("duration", "Duration", { min: 1, title: "Minutes still left" }),
    column.number("priority", "Priority", { min: 0, max: 10, title: "0 — anywhere; 1–10 — the higher, the earlier" }),
    column.deadline(),
    column.number("max_chunk_duration", "Session", { min: 1, blank: "whole", title: "Longest session, min" }),
    column.number("min_chunk_duration", "Min.", { min: 0, blank: "auto", title: "Shortest session, min" }),
    column.number("break_duration", "Break", { min: 0, title: "Break after each session, min" }),
    column.ids("After", "IDs of the tasks this one comes after"),
    column.text("description", "Description", "description_length"),
  ],
  actions: (task) => [button("⏱", "Log time spent", () => openSpend(task))],
  afterLoad: (data) => renderHistory(data.completed),
});

const routines = new Table({
  id: "routines",
  endpoint: "/api/routines",
  listKey: "routines",
  noun: "Routine",
  plural: "routines",
  fields: [
    column.name("New routine"),
    {
      label: "Type",
      title: "Fixed: at a set time. Flexible: anywhere before its deadline.",
      view: (item) => (item.type === "fixed" ? "Fixed" : "Flexible"),
      edit: (item) => select(ROUTINE_TYPES, item?.type),
      read: (td, row) => {
        row.type = td.firstChild.value;
      },
    },
    {
      // A fixed routine's start and a flexible one's deadline share a column:
      // which of the two it is follows the type.
      label: "Time",
      title: "Fixed: when it starts. Flexible: when it has to be done by.",
      view: (item) => (item.type === "fixed" ? `at ${item.time ?? "—"}` : `by ${item.deadline_time ?? "—"}`),
      edit: (item) => input("time", item ? (item.type === "fixed" ? item.time : item.deadline_time) : ""),
      read: (td, row) => {
        const value = td.firstChild.value || null;
        row.time = row.type === "fixed" ? value : null;
        row.deadline_time = row.type === "flexible" ? value : null;
      },
    },
    column.number("duration", "Duration", { min: 1, title: "Minutes" }),
    column.number("priority", "Priority", { min: 0, max: 10, title: "0 — anywhere; 1–10 — the higher, the earlier" }),
    column.number("break_duration", "Break", { min: 0, title: "Break after it, min" }),
    column.repeat([
      ["daily", "Daily"],
      ["weekly", "Weekly"],
    ]),
    column.ids("After", "IDs of the routines this one comes after"),
    {
      label: "Skipped until",
      title: "Left out of the schedule up to and including this date",
      view: (item) => skippedUntil(item),
      edit: (item) => document.createTextNode(item ? skippedUntil(item) : ""),
      read: () => {},
    },
  ],
  actions: (routine) => [button("⏸", "Skip", () => openSkip(routine))],
});

const timeBlocks = new Table({
  id: "time-blocks",
  endpoint: "/api/time-blocks",
  listKey: "time_blocks",
  noun: "Time block",
  plural: "time blocks",
  fields: [
    column.name("(optional)", false),
    column.repeat([
      ["once", "Once"],
      ["daily", "Daily"],
      ["weekly", "Weekly"],
    ]),
    column.bound("start", "Start"),
    column.bound("end", "End"),
  ],
});

const TABLES = [tasks, routines, timeBlocks];

function renderHistory(completed) {
  byId("history-count").textContent = String(completed.length);
  const items = completed.map((task) => {
    const li = document.createElement("li");
    li.textContent = `#${task.id} ${task.name}`;
    return li;
  });
  byId("history-list").replaceChildren(...items);
}

// --- Tabs ---

function showTab(name) {
  const tab = TABLES.some((table) => table.id === name) ? name : "tasks";
  for (const table of TABLES) {
    byId(`tab-${table.id}`).hidden = table.id !== tab;
    document.querySelector(`[data-tab="${table.id}"]`).setAttribute("aria-selected", String(table.id === tab));
  }
  window.history.replaceState(null, "", `#${tab}`);
}

// --- Dialogs ---

const dialogs = { spendTask: null, skipRoutine: null };

function openSpend(task) {
  dialogs.spendTask = task;
  const dialog = byId("spend-dialog");
  dialog.returnValue = "";
  byId("spend-name").textContent = task.name;
  byId("spend-minutes").value = "";
  dialog.showModal();
}

async function onSpendClosed() {
  const task = dialogs.spendTask;
  const minutes = Number(byId("spend-minutes").value);
  if (byId("spend-dialog").returnValue !== "ok" || !task || !Number.isInteger(minutes) || minutes <= 0) return;

  try {
    const result = await api("POST", `/api/tasks/${task.id}/spend`, { minutes });
    await tasks.reload();
    tasks.renderNewRow();
    setStatus(
      result.completed
        ? `“${task.name}” is done and moved to the history.`
        : `Logged ${minutes} min. ${result.remaining_min} min left.`,
    );
  } catch (error) {
    fail(error);
  }
}

function openSkip(routine) {
  dialogs.skipRoutine = routine;
  const dialog = byId("skip-dialog");
  dialog.returnValue = "";
  byId("skip-name").textContent = routine.name;
  const until = byId("skip-until");
  until.min = localToday();
  until.value = localToday();
  dialog.showModal();
}

async function onSkipClosed() {
  const routine = dialogs.skipRoutine;
  const resumeAfter = storedDate(byId("skip-until").value);
  if (byId("skip-dialog").returnValue !== "ok" || !routine || !resumeAfter) return;

  try {
    await api("POST", `/api/routines/${routine.id}/skip`, { resume_after: resumeAfter });
    await routines.reload();
    setStatus(`Routine #${routine.id} is skipped up to and including ${resumeAfter}.`);
  } catch (error) {
    fail(error);
  }
}

// --- Start ---

async function logout() {
  try {
    await api("POST", "/logout");
  } finally {
    showLogin();
  }
}

async function init() {
  byId("logout").addEventListener("click", logout);
  byId("spend-dialog").addEventListener("close", onSpendClosed);
  byId("skip-dialog").addEventListener("close", onSkipClosed);
  for (const tab of document.querySelectorAll("[data-tab]")) {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
  }

  const params = new URLSearchParams(window.location.search);
  const loginError = LOGIN_ERRORS[params.get("error")];
  if (params.has("error")) window.history.replaceState(null, "", "/");

  let me;
  try {
    me = await api("GET", "/api/me");
  } catch (error) {
    if (error.status === 403) return showLogin(LOGIN_ERRORS.no_channel);
    return showLogin(error.status === 401 ? loginError : error.message);
  }

  byId("who-name").textContent = me.name;
  byId("who").hidden = false;

  for (const table of TABLES) table.mount();
  showTab(window.location.hash.slice(1));
  show("app-view");

  try {
    await Promise.all(TABLES.map((table) => table.reload()));
  } catch (error) {
    return fail(error);
  }
  for (const table of TABLES) table.renderNewRow();
}

init();
