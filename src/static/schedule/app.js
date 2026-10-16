"use strict";

// What `/oauth/callback` sends back in `?error=` when signing in did not work.
const LOGIN_ERRORS = {
  denied: "Вхід скасовано.",
  state: "Спроба входу застаріла — спробуй ще раз.",
  discord: "Discord не відповів — спробуй ще раз трохи пізніше.",
  no_channel: "У цього акаунта немає каналу розкладу. Спершу створи його в Discord командою /schedule_channel create.",
};

// The editable columns, in table order. `blank` is what an empty cell means.
const FIELDS = [
  { key: "name", label: "Назва", type: "text" },
  { key: "duration", label: "Тривалість", type: "number", min: 1 },
  { key: "priority", label: "Пріоритет", type: "number", min: 0, max: 10 },
  { key: "deadline", label: "Дедлайн", type: "deadline" },
  { key: "max_chunk_duration", label: "Сесія", type: "number", min: 1, blank: "ціла" },
  { key: "min_chunk_duration", label: "Мін.", type: "number", min: 0, blank: "авто" },
  { key: "break_duration", label: "Перерва", type: "number", min: 0 },
  { key: "depends_on", label: "Після", type: "ids" },
  { key: "description", label: "Опис", type: "text" },
];

const state = {
  tasks: [],
  completed: [],
  defaults: {},
  limits: {},
  editingId: null,
  spendTask: null,
  busy: false,
};

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
    throw new ApiError("Сервер не відповідає.", 0);
  }

  if (response.status === 204) return null;

  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || `Помилка ${response.status}`, response.status);
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
  if (error.status === 401) return showLogin("Сесія закінчилась — увійди ще раз.");
  if (error.status === 403 && !(error.message || "").startsWith("Cross-origin")) {
    return showLogin(LOGIN_ERRORS.no_channel);
  }
  setStatus(error.message, "error");
}

// --- Deadlines ---
// The API sends ISO with the bot's own offset ("2026-10-25T18:00:00+02:00").
// Its first 16 characters are the bot's wall-clock time, which is what a
// datetime input holds and what the API takes back.

function toInputDeadline(iso) {
  return iso ? iso.slice(0, 16) : "";
}

function displayDeadline(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso || "");
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : "—";
}

function isOverdue(iso) {
  return Boolean(iso) && new Date(iso) < new Date();
}

// --- Building rows ---

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text === null || text === undefined ? "" : String(text);
  if (className) td.className = className;
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

function nameOf(id) {
  const task = state.tasks.find((t) => t.id === id);
  return task ? task.name : "(немає такої задачі)";
}

function viewRow(task) {
  const tr = document.createElement("tr");
  tr.append(cell(task.id, "num id"), cell(task.name, "name"));
  tr.append(cell(task.duration, "num"), cell(task.priority, "num"));

  const deadline = cell(displayDeadline(task.deadline), "deadline");
  if (isOverdue(task.deadline)) {
    deadline.classList.add("overdue");
    deadline.title = "Дедлайн минув";
  }
  tr.append(deadline);

  tr.append(cell(task.max_chunk_duration ?? "ціла", "num"), cell(task.min_chunk_duration ?? "—", "num"));
  tr.append(cell(task.break_duration, "num"));

  const deps = cell(task.depends_on.map((id) => `#${id}`).join(", ") || "—", "deps");
  deps.title = task.depends_on.map((id) => `#${id} ${nameOf(id)}`).join("\n");
  tr.append(deps);

  const description = cell(task.description, "description");
  description.title = task.description;
  tr.append(description);

  const actions = document.createElement("td");
  actions.className = "actions";
  actions.append(
    button("✎", "Змінити", () => startEdit(task.id)),
    button("⏱", "Записати витрачений час", () => openSpend(task)),
    button("✕", "Видалити", () => removeTask(task), "danger"),
  );
  tr.append(actions);

  tr.addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) startEdit(task.id);
  });
  return tr;
}

function inputFor(field, task) {
  const input = document.createElement("input");
  input.dataset.key = field.key;
  input.className = `in-${field.key}`;
  input.setAttribute("aria-label", field.label);
  const value = task ? task[field.key] : state.defaults[field.key];

  if (field.type === "number") {
    input.type = "number";
    input.step = "1";
    input.inputMode = "numeric";
    if (field.min !== undefined) input.min = String(field.min);
    if (field.max !== undefined) input.max = String(field.max);
    if (field.blank) input.placeholder = field.blank;
    input.value = value ?? "";
  } else if (field.type === "deadline") {
    input.type = "datetime-local";
    input.value = toInputDeadline(value);
  } else if (field.type === "ids") {
    input.type = "text";
    input.placeholder = "1, 3";
    input.value = (value || []).join(", ");
  } else {
    input.type = "text";
    input.value = value ?? "";
    if (field.key === "name") {
      input.maxLength = state.limits.name_length || 100;
      input.placeholder = "Нова задача";
    } else {
      input.maxLength = state.limits.description_length || 1000;
    }
  }
  return input;
}

function editorRow(task) {
  const isNew = task === null;
  const tr = document.createElement("tr");
  tr.className = isNew ? "new" : "editing";
  tr.append(cell(isNew ? "+" : task.id, "num id"));

  for (const field of FIELDS) {
    const td = document.createElement("td");
    td.append(inputFor(field, task));
    tr.append(td);
  }

  const save = () => saveRow(tr, isNew ? null : task.id);
  const actions = document.createElement("td");
  actions.className = "actions";
  if (isNew) {
    actions.append(button("Додати", "Додати задачу", save, "primary"));
  } else {
    actions.append(button("✓", "Зберегти", save, "primary"), button("↺", "Скасувати", cancelEdit));
  }
  tr.append(actions);

  tr.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.tagName === "INPUT") {
      event.preventDefault();
      save();
    } else if (event.key === "Escape" && !isNew) {
      cancelEdit();
    }
  });
  return tr;
}

function readRow(tr) {
  const text = (key) => tr.querySelector(`[data-key="${key}"]`).value.trim();
  const label = (key) => FIELDS.find((f) => f.key === key).label;
  const number = (key) => {
    const value = text(key);
    if (value === "") return null;
    const n = Number(value);
    if (!Number.isInteger(n)) throw new ApiError(`«${label(key)}» — це ціле число хвилин.`);
    return n;
  };

  const ids = text("depends_on").split(/[\s,;#]+/).filter(Boolean).map(Number);
  if (ids.some((n) => !Number.isInteger(n))) throw new ApiError("«Після» — це номери задач через кому.");

  return {
    name: text("name"),
    description: text("description"),
    duration: number("duration"),
    priority: number("priority"),
    deadline: text("deadline") || null,
    max_chunk_duration: number("max_chunk_duration"),
    min_chunk_duration: number("min_chunk_duration"),
    break_duration: number("break_duration"),
    depends_on: ids,
  };
}

// --- Rendering ---

function renderTasks() {
  const rows = state.tasks.map((t) => (t.id === state.editingId ? editorRow(t) : viewRow(t)));
  byId("task-rows").replaceChildren(...rows);
  byId("empty").hidden = state.tasks.length > 0;
}

// Kept apart from the rest so that what is being typed into it survives
// another row being edited or a reload.
function renderNewRow() {
  const footer = byId("new-row");
  if (state.tasks.length >= (state.limits.max_tasks || Infinity)) {
    const tr = document.createElement("tr");
    const td = cell(`Досягнуто ліміту в ${state.limits.max_tasks} задач.`, "muted");
    td.colSpan = FIELDS.length + 2;
    tr.append(td);
    footer.replaceChildren(tr);
  } else {
    footer.replaceChildren(editorRow(null));
  }
}

function renderHistory() {
  byId("history-count").textContent = String(state.completed.length);
  const items = state.completed.map((t) => {
    const li = document.createElement("li");
    li.textContent = `#${t.id} ${t.name}`;
    return li;
  });
  byId("history-list").replaceChildren(...items);
}

async function reload() {
  const data = await api("GET", "/api/tasks");
  Object.assign(state, {
    tasks: data.tasks,
    completed: data.completed,
    defaults: data.defaults,
    limits: data.limits,
  });
  if (!state.tasks.some((t) => t.id === state.editingId)) state.editingId = null;
  renderTasks();
  renderHistory();
}

// --- Actions ---

function startEdit(id) {
  state.editingId = id;
  renderTasks();
  const input = byId("task-rows").querySelector("tr.editing .in-name");
  if (input) input.focus();
}

function cancelEdit() {
  state.editingId = null;
  renderTasks();
}

async function saveRow(tr, id) {
  if (state.busy) return;

  let payload;
  try {
    payload = readRow(tr);
  } catch (error) {
    return fail(error);
  }

  state.busy = true;
  tr.classList.add("saving");
  try {
    if (id === null) {
      const created = await api("POST", "/api/tasks", payload);
      await reload();
      renderNewRow();
      setStatus(`Додано задачу #${created.id}.`);
      byId("new-row").querySelector(".in-name")?.focus();
    } else {
      await api("PUT", `/api/tasks/${id}`, payload);
      state.editingId = null;
      await reload();
      setStatus(`Задачу #${id} збережено.`);
    }
  } catch (error) {
    fail(error);
  } finally {
    state.busy = false;
    tr.classList.remove("saving");
  }
}

async function removeTask(task) {
  if (!window.confirm(`Видалити задачу #${task.id} «${task.name}»?`)) return;
  try {
    await api("DELETE", `/api/tasks/${task.id}`);
    await reload();
    renderNewRow();
    setStatus(`Задачу #${task.id} видалено.`);
  } catch (error) {
    fail(error);
  }
}

function openSpend(task) {
  state.spendTask = task;
  const dialog = byId("spend-dialog");
  dialog.returnValue = "";
  byId("spend-name").textContent = task.name;
  byId("spend-minutes").value = "";
  dialog.showModal();
}

async function onSpendClosed() {
  const task = state.spendTask;
  const minutes = Number(byId("spend-minutes").value);
  if (byId("spend-dialog").returnValue !== "ok" || !task || !Number.isInteger(minutes) || minutes <= 0) return;

  try {
    const result = await api("POST", `/api/tasks/${task.id}/spend`, { minutes });
    await reload();
    renderNewRow();
    setStatus(
      result.completed
        ? `«${task.name}» виконано — задача в історії.`
        : `Записано ${minutes} хв. Лишилось ${result.remaining_min} хв.`,
    );
  } catch (error) {
    fail(error);
  }
}

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

  try {
    await reload();
  } catch (error) {
    show("app-view");
    return fail(error);
  }
  renderNewRow();
  show("app-view");
}

init();
