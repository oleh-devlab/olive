"""Cutting one user's schedule into pager-sized pages.

Takes the days `schedule_timeline` built and gives back pages the pager turns.

The pager renders a day bottom-up — its last block on top, its first at the
bottom — so the reader's eye ends on the start of the day. That flip is applied
per page, *after* the day has been cut: cutting the already-flipped blocks would
fill page 1 with the evening and leave the morning for page 2.

Pure text handling — no disnake, no settings, no filesystem — so the cog above it
stays Discord-only and this stays unit-testable.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.schedule_timeline import ScheduleDay

# Discord's own cap on a message's content. The inflation side keeps its own copy
# of this number — the two subsystems render nothing in common and do not import
# each other.
MESSAGE_LIMIT = 2000

# A page below this many characters is not worth turning to, so the frame around
# it gets trimmed instead of the schedule.
MIN_PAGE_CHARS = 300

# A frame is priced before the pages exist, so its page counter is measured as
# "1/1". This is the room a counter that grows to three digits needs on top.
PAGE_COUNTER_RESERVE = 8

# The "didn't fit" note is one line under the schedule, and every character it
# takes is one the schedule does not get. Hence the short labels, the ranges over
# id lists and `+N` over spelling out how many entries were cut.
NOTE_FRAME = "\n*Didn't fit — {body}*"
NOTE_SEPARATOR = " · "
TASKS_LABEL = "tasks: "
# Routine names are free text and can hold a comma, so they are not joined by one.
ROUTINES_LABEL = "routines: "
ROUTINES_SEPARATOR = "; "


@dataclass(slots=True)
class SchedulePage:
    """One page: its text, the routines shown on it and the day it covers."""

    content: str
    routine_ids: set = field(default_factory=set)
    date: datetime.date | None = None


def invert_schedule_blocks(blocks: list[str]) -> list[str]:
    """Inverts the chronological order of blocks and lines for a bottom-up view."""
    return ["\n".join(reversed(block.split("\n"))) for block in reversed(blocks)]


def frame_cost(frame: str, header: str = "") -> int:
    """What one page's frame costs, measured rather than guessed at.

    `frame` is the caller's own rendering around an empty body — its format
    string comes from `phrases.json` and an operator can rewrite it to any
    length, so it is priced by rendering it. `header` is what the page source
    prepends to every page, blank line included.
    """
    return len(frame) + (len(header) + 2 if header else 0) + PAGE_COUNTER_RESERVE


def page_char_limit(frame_cost: int) -> int:
    """How many characters of schedule are left for a page once its frame is paid for.

    `frame_cost` is what the caller measured, not guessed at: the status header
    comes from `phrases.json` and an operator can rewrite it to any length. The
    floor keeps a page readable even then — an over-long frame is trimmed by
    `trim_to_whole_lines` rather than allowed to eat the whole page.
    """
    return max(MIN_PAGE_CHARS, MESSAGE_LIMIT - frame_cost)


def fit_items(
    items: list[str],
    budget: int,
    separator: str = "\n",
    overflow: Callable[[int], str] | None = None,
) -> str:
    """Join as many whole items as fit into `budget`, ending on a note about the rest.

    Used for the lists below the schedule — skipped task ids, skipped routines —
    which grow with the user's data and would otherwise push the message past
    Discord's limit, which costs the reader the whole message rather than the
    tail of a list. Returns "" when not even the note fits.
    """
    kept: list[str] = []
    used = 0

    for index, item in enumerate(items):
        gap = len(separator) if kept else 0
        # Room for the note this item would need if it were the last one shown.
        left_behind = len(items) - index - 1
        note = overflow(left_behind) if overflow and left_behind else ""
        reserved = len(separator) + len(note) if note else 0

        if used + gap + len(item) + reserved > budget:
            break

        kept.append(item)
        used += gap + len(item)
    else:
        return separator.join(kept)

    note = overflow(len(items) - len(kept)) if overflow else ""
    text = separator.join([*kept, note] if note else kept)

    return text if len(text) <= budget else ""


def trim_to_whole_lines(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """The longest run of whole lines of `text` that fits into `limit`.

    The last resort, for a frame an operator made longer than the whole message
    may be: Discord refuses the edit outright, which would freeze the channel's
    message on whatever it showed before.
    """
    if len(text) <= limit:
        return text

    head = text[:limit]
    last_break = head.rfind("\n")

    return head[:last_break] if last_break > 0 else head


def _split_blocks(blocks: list[str], budget: int) -> list[list[str]]:
    """Greedily group chronological blocks into runs that fit into `budget` characters.

    A single block longer than the budget gets a page of its own — nothing here
    can make it smaller, and breaking it apart would cut a timeline block in half.
    """
    groups: list[list[str]] = []
    current: list[str] = []
    current_len = 0

    for block in blocks:
        separator = 1 if current else 0

        if current and current_len + separator + len(block) > budget:
            groups.append(current)
            current = [block]
            current_len = len(block)
        else:
            current.append(block)
            current_len += separator + len(block)

    if current:
        groups.append(current)

    return groups


def _day_header(day: "ScheduleDay", part: int = 0, total_parts: int = 0) -> str:
    """`=== 01.01.2026 (Thursday) ===`, plus a part marker when the day was split."""
    header = f"=== {day.date_str} ({day.weekday})"

    if total_parts > 1:
        header += f" (Part {part}/{total_parts})"

    return f"{header} ==="


def paginate_days(days: list["ScheduleDay"], char_limit: int) -> list[SchedulePage]:
    """One `SchedulePage` per pager page, days and parts alike in chronological order.

    `char_limit` is what a page's text may cost — `page_char_limit()` works it out
    from the frame the caller is going to render around it.
    """
    pages: list[SchedulePage] = []

    for day in days:
        # The part marker is measured against the same budget as the plain
        # header; it is a dozen characters against the slack `char_limit`
        # already leaves below Discord's own limit.
        groups = _split_blocks(day.blocks, char_limit - len(_day_header(day)) - 1)
        total_parts = len(groups)

        for part, group in enumerate(groups, start=1):
            header = _day_header(day, part, total_parts)
            body = "\n".join(invert_schedule_blocks(group))

            pages.append(
                SchedulePage(
                    content=f"{header}\n{body}",
                    routine_ids=day.routine_ids,
                    date=day.date,
                )
            )

    return pages


def _more(left: int) -> str:
    """How a trimmed list says what it left out."""
    return f"+{left}"


def compress_id_runs(ids: list[int]) -> list[str]:
    """Sorted ids with consecutive runs collapsed: 12, 13, 14, 20 becomes `12-14`, `20`.

    Ids the solver skipped come in runs more often than not — a whole horizon of
    a repeating task — and a run costs the same three or four characters however
    long it is.
    """
    runs: list[list[int]] = []

    for task_id in sorted(set(ids)):
        if runs and task_id == runs[-1][-1] + 1:
            runs[-1].append(task_id)
        else:
            runs.append([task_id])

    return [str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs]


def build_notes(skipped_task_ids: list[int], skipped_routines: list[str], frame_cost: int) -> str:
    """The one-line "didn't fit" note under the schedule, in what room is left for it.

    Both lists grow with the user's data, so both are measured against what the
    frame leaves once a page keeps its minimum. Discord refuses an over-long
    message instead of truncating it, and a reader would lose the schedule
    itself over the list of what it left out.
    """
    room = MESSAGE_LIMIT - frame_cost - MIN_PAGE_CHARS - len(NOTE_FRAME.format(body=""))
    if room <= 0:
        return ""

    parts: list[str] = []

    if skipped_task_ids:
        # A runaway list of ids must not crowd the routines out entirely: they
        # are named, and a name says more than a hundredth id does.
        ids_room = room // 2 if skipped_routines else room
        ids = fit_items(compress_id_runs(skipped_task_ids), ids_room - len(TASKS_LABEL), ", ", _more)
        if ids:
            parts.append(TASKS_LABEL + ids)

    if skipped_routines:
        used = len(parts[0]) + len(NOTE_SEPARATOR) if parts else 0
        routines = fit_items(
            skipped_routines,
            room - used - len(ROUTINES_LABEL),
            ROUTINES_SEPARATOR,
            _more,
        )
        if routines:
            parts.append(ROUTINES_LABEL + routines)

    return NOTE_FRAME.format(body=NOTE_SEPARATOR.join(parts)) if parts else ""
