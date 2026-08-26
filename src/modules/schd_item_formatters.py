"""Listing a user's tasks, blocks and routines for whoever is reading.

The same listing goes to two readers: a person in Discord, who gets markdown,
and the agent, which gets plain text. Which of the two is not a branch inside
every function — it is a `Style` handed in, so each function below states the
listing once and nothing here asks who is looking.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Style:
    """How a listing is dressed for its reader.

    Discord also heads a listing with what it is and pairs short fields onto one
    line; the agent gets neither, because the tool it called already says what it
    asked for, and vertical space costs it nothing.
    """

    bold: str = ""
    code: str = ""
    headed: bool = False
    # Empty means one field per line.
    field_separator: str = ""

    def b(self, text: str) -> str:
        return f"{self.bold}{text}{self.bold}"

    def c(self, text: str) -> str:
        return f"{self.code}{text}{self.code}"

    def heading(self, text: str) -> list[str]:
        """The line a listing opens with, or nothing at all."""
        return [self.b(text)] if self.headed else []

    def title(self, headline: str, bare: str) -> str:
        """A detail view's first line: dressed for a reader, stated for the agent."""
        return self.b(headline) if self.headed else bare

    def fields(self, pairs: Iterable[tuple[str, str]]) -> list[str]:
        """Short `name: value` pairs, together on one line or one per line."""
        lines = [f"{self.b(f'{name}:')} {value}" for name, value in pairs]

        return [self.field_separator.join(lines)] if self.field_separator else lines


PLAIN = Style()
DISCORD = Style(bold="**", code="`", headed=True, field_separator="  |  ")


def _mins(td) -> int:
    """Хелпер для швидкої конвертації timedelta у хвилини."""
    return int(td.total_seconds() // 60) if td else 0


def _depends_on(item) -> str:
    depends_on = getattr(item, "depends_on", None)

    return f" (Depends on: {', '.join(map(str, depends_on))})" if depends_on else ""


def format_task_list(tasks, style: Style = PLAIN) -> str:
    if not tasks:
        return "No tasks found."

    lines = style.heading("Your Tasks:")
    lines.extend(
        f"{style.c(f'[ID: {t.id}]')} {style.b(t.name)} - {_mins(t.duration)} min "
        f"(Priority: {t.priority}){_depends_on(t)}"
        for t in tasks
    )

    return "\n".join(lines)


def format_completed_task_list(tasks, style: Style = PLAIN) -> str:
    if not tasks:
        return "No completed tasks found in history."

    lines = style.heading("Completed Tasks:")
    lines.extend(f"{style.c(f'[ID: {t.id}]')} {style.b(t.name)} (Priority: {t.priority})" for t in tasks)

    return "\n".join(lines)


def format_task_info(task, style: Style = PLAIN) -> str:
    if not task:
        return "Task not found."

    description = task.description.strip() if getattr(task, "description", None) and task.description.strip() else ""
    session_mins = _mins(task.max_chunk_duration) if getattr(task, "max_chunk_duration", None) else "N/A"
    deadline = task.deadline.strftime("%d.%m.%Y %H:%M") if getattr(task, "deadline", None) else "none"

    lines = [
        style.title(f"Task Details (ID: {task.id})", f"ID: {task.id}"),
        f"{style.b('Name:')} {task.name}",
        f"{style.b('Description:')} {description or '(none)'}",
        f"{style.b('Deadline:')} {deadline}",
        f"{style.b('Priority:')} {task.priority}",
        f"{style.b('Total Duration:')} {_mins(task.duration)} min",
    ]
    lines.extend(
        style.fields(
            [("Session", f"{session_mins} min"), ("Break", f"{_mins(getattr(task, 'break_duration', None))} min")]
        )
    )

    if getattr(task, "min_chunk_duration", None):
        lines.append(f"{style.b('Min session shortening allowed:')} {_mins(task.min_chunk_duration)} min")

    return "\n".join(lines)


def format_timeblock_list(blocks, style: Style = PLAIN) -> str:
    if not blocks:
        return "No time blocks found."

    lines = style.heading("Your Time Blocks:")

    for blk in blocks:
        block_id = style.c(f"[ID: {getattr(blk, 'id', '?')}]")

        try:
            start = blk.start.strftime("%H:%M") if hasattr(blk.start, "strftime") else "???"
            end = blk.end.strftime("%H:%M") if hasattr(blk.end, "strftime") else "???"
            repeat = "Daily" if getattr(blk, "daily", False) else "One-time"
            name = f" {style.b(blk.name)}" if getattr(blk, "name", None) else ""
            lines.append(f"{block_id}{name} {start} - {end} ({repeat})")
        except Exception:
            lines.append(f"{block_id} Invalid Block Data")

    return "\n".join(lines)


def format_routine_list(routines, style: Style = PLAIN) -> str:
    if not routines:
        return "No routines found."

    lines = style.heading("Your Routines:")

    for r in routines:
        at_time = ""
        if r.type == "fixed" and getattr(r, "time", None):
            at_time = f" @ {r.time.strftime('%H:%M')}"
        elif r.type == "flexible" and getattr(r, "deadline_time", None):
            at_time = f" by {r.deadline_time.strftime('%H:%M')}"

        repeat = f"weekly on {r.weekdays}" if r.repeat == "weekly" and getattr(r, "weekdays", None) else r.repeat
        skip = f" [Resumes after {r.resume_after.strftime('%d.%m.%Y')}]" if getattr(r, "resume_after", None) else ""

        lines.append(
            f"{style.c(f'[ID: {r.id}]')} {style.b(r.name)} "
            f"({r.type}, {repeat}, {_mins(r.duration)}m){at_time}{_depends_on(r)}{skip}"
        )

    return "\n".join(lines)


def format_routine_info(routine, style: Style = PLAIN) -> str:
    if not routine:
        return "Routine not found."

    lines = [
        style.title(f"Routine Details (ID: {routine.id})", f"ID: {routine.id}"),
        f"{style.b('Name:')} {routine.name}",
        f"{style.b('Type:')} {routine.type}",
        f"{style.b('Repeat:')} {routine.repeat}",
    ]

    if routine.repeat == "weekly" and getattr(routine, "weekdays", None):
        lines.append(f"{style.b('Weekdays:')} {routine.weekdays} (0=Mon, 6=Sun)")

    if routine.type == "fixed" and getattr(routine, "time", None):
        lines.append(f"{style.b('Time:')} {routine.time.strftime('%H:%M')}")
    elif routine.type == "flexible" and getattr(routine, "deadline_time", None):
        lines.append(f"{style.b('Deadline:')} {routine.deadline_time.strftime('%H:%M')}")

    lines.append(f"{style.b('Duration:')} {_mins(routine.duration)} min")
    lines.append(f"{style.b('Break Duration:')} {_mins(getattr(routine, 'break_duration', None))} min")
    lines.append(f"{style.b('Priority:')} {routine.priority}")

    if getattr(routine, "depends_on", None):
        lines.append(f"{style.b('Depends On:')} {', '.join(map(str, routine.depends_on))}")
    if getattr(routine, "resume_after", None):
        lines.append(f"{style.b('Resumes after:')} {routine.resume_after.strftime('%d.%m.%Y')}")

    return "\n".join(lines)
