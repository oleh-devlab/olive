import { useEffect, useRef } from "react";

import { localToday, storedDate } from "./dates";
import type { Routine, Task } from "./types";

// Each dialog opens when it is given an item and reports back once, when it
// closes: with what was entered, or null when it was cancelled.

export function SpendDialog({ task, onDone }: { task: Task | null; onDone: (minutes: number | null) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const minutes = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!task || !dialog.current || !minutes.current) return;
    minutes.current.value = "";
    dialog.current.returnValue = "";
    dialog.current.showModal();
  }, [task]);

  return (
    <dialog
      id="spend-dialog"
      ref={dialog}
      onClose={() => {
        const value = Number(minutes.current?.value);
        const ok = dialog.current?.returnValue === "ok" && Number.isInteger(value) && value > 0;
        onDone(ok ? value : null);
      }}
    >
      <form method="dialog">
        <p>
          How many minutes did you spend on “<span id="spend-name">{task?.name}</span>”?
        </p>
        <input id="spend-minutes" ref={minutes} type="number" min={1} step={1} inputMode="numeric" required />
        <div className="dialog-actions">
          <button type="submit" value="cancel" formNoValidate>
            Cancel
          </button>
          <button type="submit" value="ok" className="primary">
            Save
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function SkipDialog({ routine, onDone }: { routine: Routine | null; onDone: (resumeAfter: string | null) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const until = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!routine || !dialog.current || !until.current) return;
    until.current.min = localToday();
    until.current.value = localToday();
    dialog.current.returnValue = "";
    dialog.current.showModal();
  }, [routine]);

  return (
    <dialog
      id="skip-dialog"
      ref={dialog}
      onClose={() => {
        const resumeAfter = storedDate(until.current?.value ?? "");
        onDone(dialog.current?.returnValue === "ok" ? resumeAfter : null);
      }}
    >
      <form method="dialog">
        <p>
          Skip “<span id="skip-name">{routine?.name}</span>” up to and including:
        </p>
        <input id="skip-until" ref={until} type="date" required />
        <div className="dialog-actions">
          <button type="submit" value="cancel" formNoValidate>
            Cancel
          </button>
          <button type="submit" value="ok" className="primary">
            Skip
          </button>
        </div>
      </form>
    </dialog>
  );
}
