import { useCallback, useEffect, useState, type ReactNode } from "react";

import { api } from "./api";
import type { Column, Context, Draft, Row, Shown } from "./columns";
import type { Defaults, Item, Limits, Listing } from "./types";

export interface TableConfig<T extends Item> {
  id: string;
  endpoint: string;
  listKey: string;
  noun: string;
  plural: string;
  empty: string;
  // Said under the table, such as what a shortened heading stands for.
  note?: string;
  columns: Column<T>[];
}

interface Props<T extends Item> {
  config: TableConfig<T>;
  notify: (text: string) => void;
  fail: (error: unknown) => void;
  actions?: (item: T) => ReactNode;
  onLoaded?: (data: Listing) => void;
  // Told after the table has changed its items, which changes the schedule.
  onChange?: () => void;
  // Bumped by the page when something outside the table changed its items.
  version?: number;
  // Whether that also starts the new row afresh, as adding or deleting does.
  renewOnVersion?: boolean;
}

export function Button(props: { text: string; title: string; onClick: () => void; className?: string }) {
  return (
    <button type="button" title={props.title} aria-label={props.title} className={props.className} onClick={props.onClick}>
      {props.text}
    </button>
  );
}

function shown(value: Shown): { text: string | number; className?: string; title?: string } {
  return typeof value === "object" ? value : { text: value };
}

function classes(...names: (string | false | undefined)[]): string | undefined {
  return names.filter(Boolean).join(" ") || undefined;
}

export function Table<T extends Item>({
  config,
  notify,
  fail,
  actions,
  onLoaded,
  onChange,
  version = 0,
  renewOnVersion = false,
}: Props<T>) {
  const { columns, endpoint, listKey, noun } = config;
  const [items, setItems] = useState<T[]>([]);
  const [limits, setLimits] = useState<Limits>({});
  const [defaults, setDefaults] = useState<Defaults>({});
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState<{ id: number; draft: Draft } | null>(null);
  // Kept apart from the rest so that what is being typed into it survives
  // another row being edited or a reload -- but not a change in how many
  // items there are, which may bring the limit up in its place.
  const [newDraft, setNewDraft] = useState<Draft | null>(null);
  // How many items this page has added: each one brings up a fresh new row,
  // focused for the next.
  const [added, setAdded] = useState(0);
  const [saving, setSaving] = useState<number | "new" | null>(null);

  const context: Context = { items, limits, defaults };

  const initDraft = useCallback(
    (item: T | null, ctx: Context): Draft => Object.assign({}, ...columns.map((column) => column.init(item, ctx))),
    [columns],
  );

  const apply = useCallback(
    (data: Listing, renew: boolean): Context => {
      const list = data[listKey] as T[];
      const ctx: Context = { items: list, limits: data.limits ?? {}, defaults: data.defaults ?? {} };
      setItems(list);
      setLimits(ctx.limits);
      setDefaults(ctx.defaults);
      setLoaded(true);
      // A row being edited starts again from what the server now holds: what
      // was typed into it may no longer fit, such as a dependency on an item
      // just deleted.
      setEditing((current) => {
        const item = current && list.find((candidate) => candidate.id === current.id);
        return item ? { id: item.id, draft: initDraft(item, ctx) } : null;
      });
      setNewDraft((current) => (renew || !current ? initDraft(null, ctx) : current));
      onLoaded?.(data);
      return ctx;
    },
    [listKey, initDraft, onLoaded],
  );

  const load = async (renew = false) => apply(await api<Listing>("GET", endpoint), renew);

  useEffect(() => {
    let cancelled = false;
    api<Listing>("GET", endpoint).then((data) => {
      if (!cancelled) apply(data, renewOnVersion);
    }, fail);
    return () => {
      cancelled = true;
    };
  }, [endpoint, apply, fail, version, renewOnVersion]);

  function readDraft(draft: Draft): Row {
    const row: Row = {};
    for (const column of columns) column.read(draft, row);
    return row;
  }

  async function save(draft: Draft, id: number | null) {
    if (saving !== null) return;

    let row: Row;
    try {
      row = readDraft(draft);
    } catch (error) {
      return fail(error);
    }

    setSaving(id ?? "new");
    try {
      if (id === null) {
        const created = await api<T>("POST", endpoint, row);
        await load(true);
        setAdded((count) => count + 1);
        onChange?.();
        notify(`${noun} #${created.id} added.`);
      } else {
        await api("PUT", `${endpoint}/${id}`, row);
        setEditing(null);
        await load();
        onChange?.();
        notify(`${noun} #${id} saved.`);
      }
    } catch (error) {
      fail(error);
    } finally {
      setSaving(null);
    }
  }

  async function remove(item: T) {
    const label = item.name ? ` “${item.name}”` : "";
    if (!window.confirm(`Delete ${noun.toLowerCase()} #${item.id}${label}?`)) return;
    try {
      await api("DELETE", `${endpoint}/${item.id}`);
      await load(true);
      onChange?.();
      notify(`${noun} #${item.id} deleted.`);
    } catch (error) {
      fail(error);
    }
  }

  function startEdit(item: T) {
    setEditing({ id: item.id, draft: initDraft(item, context) });
  }

  function viewRow(item: T) {
    return (
      <tr
        key={item.id}
        onDoubleClick={(event) => {
          if (!(event.target as HTMLElement).closest("button")) startEdit(item);
        }}
      >
        <td className="num id">{item.id}</td>
        {columns.map((column, index) => {
          const cell = shown(column.view(item, context));
          return (
            <td
              key={index}
              className={classes(column.numeric && "num", column.className, cell.className, narrow[index] && "narrow")}
              title={cell.title || undefined}
            >
              {cell.text}
            </td>
          );
        })}
        <td className="actions">
          <button type="button" title="Edit" aria-label="Edit" onClick={() => startEdit(item)}>
            ✎
          </button>
          {actions?.(item)}
          <button type="button" title="Delete" aria-label="Delete" className="danger" onClick={() => void remove(item)}>
            ✕
          </button>
        </td>
      </tr>
    );
  }

  // A row being edited mounts afresh, so its first input takes the focus; so
  // does the new row, but only once something has been added through it --
  // not when the page first loads.
  function editorRow(draft: Draft, id: number | null) {
    const isNew = id === null;
    const focusFirst = !isNew || added > 0;
    const set = isNew
      ? (patch: Draft) => setNewDraft((current) => ({ ...current, ...patch }))
      : (patch: Draft) => setEditing((current) => current && { ...current, draft: { ...current.draft, ...patch } });

    return (
      <tr
        key={isNew ? `new-${added}` : `edit-${id}`}
        className={classes(isNew ? "new" : "editing", saving === (id ?? "new") && "saving")}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.target as HTMLElement).tagName === "INPUT") {
            event.preventDefault();
            void save(draft, id);
          } else if (event.key === "Escape" && !isNew) {
            setEditing(null);
          }
        }}
      >
        <td className="num id">{isNew ? "+" : id}</td>
        {columns.map((column, index) => (
          <td key={index} className={classes(narrow[index] && "narrow")}>
            {column.edit(draft, set, context, focusFirst && index === 0)}
          </td>
        ))}
        <td className="actions">
          {isNew ? (
            <button
              type="button"
              title={`Add ${noun.toLowerCase()}`}
              aria-label={`Add ${noun.toLowerCase()}`}
              className="primary"
              onClick={() => void save(draft, id)}
            >
              Add
            </button>
          ) : (
            <>
              <button type="button" title="Save" aria-label="Save" className="primary" onClick={() => void save(draft, id)}>
                ✓
              </button>
              <button type="button" title="Cancel" aria-label="Cancel" onClick={() => setEditing(null)}>
                ↺
              </button>
            </>
          )}
        </td>
      </tr>
    );
  }

  const atLimit = items.length >= (limits.max_items ?? Infinity);
  // The columns no item has anything in, which narrow to leave the name more room.
  const narrow = columns.map((column) => Boolean(column.emptyLabel && column.isEmpty && items.every(column.isEmpty)));

  return (
    <>
      <div className="table-wrap">
        <table id={`table-${config.id}`}>
          <thead>
            <tr>
              <th scope="col" className="num">
                ID
              </th>
              {columns.map((column, index) => (
                <th
                  key={index}
                  scope="col"
                  className={classes(column.numeric && "num", column.className, narrow[index] && "narrow")}
                  title={column.title ?? (narrow[index] ? column.label : undefined)}
                >
                  {narrow[index] ? column.emptyLabel : column.label}
                </th>
              ))}
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>{items.map((item) => (editing?.id === item.id ? editorRow(editing.draft, item.id) : viewRow(item)))}</tbody>
          <tfoot>
            {loaded && atLimit && (
              <tr>
                <td className="muted" colSpan={columns.length + 2}>
                  You have reached the limit of {limits.max_items} {config.plural}.
                </td>
              </tr>
            )}
            {loaded && !atLimit && newDraft && editorRow(newDraft, null)}
          </tfoot>
        </table>
      </div>
      <p className="muted empty" hidden={!loaded || items.length > 0}>
        {config.empty}
      </p>
      {config.note && <p className="muted table-note">{config.note}</p>}
    </>
  );
}
