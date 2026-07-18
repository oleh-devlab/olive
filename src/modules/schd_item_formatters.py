def _mins(td) -> int:
    """Хелпер для швидкої конвертації timedelta у хвилини."""
    return int(td.total_seconds() // 60) if td else 0


def format_task_list(tasks, use_markdown: bool = False) -> str:
    if not tasks:
        return "No tasks found."

    b, c = ("**", "`") if use_markdown else ("", "")
    lines = [f"{b}Your Tasks:{b}"] if use_markdown else []

    for t in tasks:
        deps = f" (Depends on: {', '.join(map(str, t.depends_on))})" if getattr(t, "depends_on", None) else ""
        lines.append(f"{c}[ID: {t.id}]{c} {b}{t.name}{b} - {_mins(t.duration)} min (Priority: {t.priority}){deps}")

    return "\n".join(lines)


def format_completed_task_list(tasks, use_markdown: bool = False) -> str:
    if not tasks:
        return "No completed tasks found in history."

    b, c = ("**", "`") if use_markdown else ("", "")
    lines = [f"{b}Completed Tasks:{b}"] if use_markdown else []

    for t in tasks:
        lines.append(f"{c}[ID: {t.id}]{c} {b}{t.name}{b} (Priority: {t.priority})")

    return "\n".join(lines)


def format_task_info(task, use_markdown: bool = False) -> str:
    if not task:
        return "Task not found."

    b = "**" if use_markdown else ""
    desc = task.description.strip() if getattr(task, "description", None) and task.description.strip() else "(none)"
    session_mins = _mins(task.max_chunk_duration) if getattr(task, "max_chunk_duration", None) else "N/A"
    dl_str = task.deadline.strftime("%d.%m.%Y %H:%M") if getattr(task, "deadline", None) else "none"

    lines = [
        f"{b}Task Details (ID: {task.id}){b}" if use_markdown else f"ID: {task.id}",
        f"{b}Name:{b} {task.name}",
        f"{b}Description:{b} {desc}",
        f"{b}Deadline:{b} {dl_str}",
        f"{b}Priority:{b} {task.priority}",
        f"{b}Total Duration:{b} {_mins(task.duration)} min",
    ]

    if use_markdown:
        lines.append(
            f"{b}Session:{b} {session_mins} min  |  {b}Break:{b} {_mins(getattr(task, 'break_duration', None))} min"
        )
    else:
        lines.append(f"Session: {session_mins} min")
        lines.append(f"Break: {_mins(getattr(task, 'break_duration', None))} min")

    if getattr(task, "min_chunk_duration", None):
        lines.append(f"{b}Min session shortening allowed:{b} {_mins(task.min_chunk_duration)} min")

    return "\n".join(lines)


def format_timeblock_list(blocks, use_markdown: bool = False) -> str:
    if not blocks:
        return "No time blocks found."

    b, c = ("**", "`") if use_markdown else ("", "")
    lines = [f"{b}Your Time Blocks:{b}"] if use_markdown else []

    for i, blk in enumerate(blocks):
        try:
            st = blk.start.strftime("%H:%M") if hasattr(blk.start, "strftime") else "???"
            et = blk.end.strftime("%H:%M") if hasattr(blk.end, "strftime") else "???"
            rep = "Daily" if getattr(blk, "daily", False) else "One-time"
            name_str = f" {b}{blk.name}{b}" if getattr(blk, "name", None) else ""
            b_id = getattr(blk, "id", "?")
            lines.append(f"{c}[ID: {b_id}]{c}{name_str} {st} - {et} ({rep})")
        except Exception:
            b_id = getattr(blk, "id", "?")
            lines.append(f"{c}[ID: {b_id}]{c} Invalid Block Data")

    return "\n".join(lines)


def format_routine_list(routines, use_markdown: bool = False) -> str:
    if not routines:
        return "No routines found."

    b, c = ("**", "`") if use_markdown else ("", "")
    lines = [f"{b}Your Routines:{b}"] if use_markdown else []

    for r in routines:
        t_str = ""
        if r.type == "fixed" and getattr(r, "time", None):
            t_str = f" @ {r.time.strftime('%H:%M')}"
        elif r.type == "flexible" and getattr(r, "deadline_time", None):
            t_str = f" by {r.deadline_time.strftime('%H:%M')}"

        rep = f"weekly on {r.weekdays}" if r.repeat == "weekly" and getattr(r, "weekdays", None) else r.repeat
        deps = f" (Depends on: {', '.join(map(str, r.depends_on))})" if getattr(r, "depends_on", None) else ""
        skip = f" [Resumes after {r.resume_after.strftime('%d.%m.%Y')}]" if getattr(r, "resume_after", None) else ""

        lines.append(f"{c}[ID: {r.id}]{c} {b}{r.name}{b} ({r.type}, {rep}, {_mins(r.duration)}m){t_str}{deps}{skip}")

    return "\n".join(lines)


def format_routine_info(routine, use_markdown: bool = False) -> str:
    if not routine:
        return "Routine not found."

    b = "**" if use_markdown else ""
    lines = [
        f"{b}Routine Details (ID: {routine.id}){b}" if use_markdown else f"ID: {routine.id}",
        f"{b}Name:{b} {routine.name}",
        f"{b}Type:{b} {routine.type}",
        f"{b}Repeat:{b} {routine.repeat}",
    ]

    if routine.repeat == "weekly" and getattr(routine, "weekdays", None):
        lines.append(f"{b}Weekdays:{b} {routine.weekdays} (0=Mon, 6=Sun)")

    if routine.type == "fixed" and getattr(routine, "time", None):
        lines.append(f"{b}Time:{b} {routine.time.strftime('%H:%M')}")
    elif routine.type == "flexible" and getattr(routine, "deadline_time", None):
        lines.append(f"{b}Deadline:{b} {routine.deadline_time.strftime('%H:%M')}")

    lines.append(f"{b}Duration:{b} {_mins(routine.duration)} min")
    lines.append(f"{b}Break Duration:{b} {_mins(getattr(routine, 'break_duration', None))} min")
    lines.append(f"{b}Priority:{b} {routine.priority}")
    if getattr(routine, "depends_on", None):
        lines.append(f"{b}Depends On:{b} {', '.join(map(str, routine.depends_on))}")
    if getattr(routine, "resume_after", None):
        lines.append(f"{b}Resumes after:{b} {routine.resume_after.strftime('%d.%m.%Y')}")

    return "\n".join(lines)
