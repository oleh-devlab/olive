"""Cutting one user's schedule into pager-sized pages.

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

# Discord's own cap on a message's content. The inflation side keeps its own copy
# of this number — the two subsystems render nothing in common and do not import
# each other.
MESSAGE_LIMIT = 2000

# A page below this many characters is not worth turning to, so the frame around
# it gets trimmed instead of the schedule.
MIN_PAGE_CHARS = 300

TASKS_NOTE = "\n\n*Tasks that didn't fit (IDs): {ids}*"
ROUTINES_NOTE_HEADING = "\n*Skipped routines:*\n"


@dataclass(slots=True)
class SchedulePage:
    """One page: its text, the routines shown on it and the day it covers."""

    content: str
    routine_ids: set = field(default_factory=set)
    date: datetime.date | None = None


def invert_schedule_blocks(blocks: list[str]) -> list[str]:
    """Inverts the chronological order of blocks and lines for a bottom-up view."""
    return ["\n".join(reversed(block.split("\n"))) for block in reversed(blocks)]


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


def _day_header(day: dict, part: int = 0, total_parts: int = 0) -> str:
    """`=== 01.01.2026 (Thursday) ===`, plus a part marker when the day was split."""
    header = f"=== {day['date_str']} ({day['weekday']})"

    if total_parts > 1:
        header += f" (Part {part}/{total_parts})"

    return f"{header} ==="


def paginate_days(days: list[dict], char_limit: int) -> list[SchedulePage]:
    """One `SchedulePage` per pager page, days and parts alike in chronological order.

    `char_limit` is what a page's text may cost — `page_char_limit()` works it out
    from the frame the caller is going to render around it.
    """
    pages: list[SchedulePage] = []

    for day in days:
        blocks = day.get("blocks") or []
        # The part marker is measured against the same budget as the plain
        # header; it is a dozen characters against the slack `char_limit`
        # already leaves below Discord's own limit.
        groups = _split_blocks(blocks, char_limit - len(_day_header(day)) - 1)
        total_parts = len(groups)

        for part, group in enumerate(groups, start=1):
            header = _day_header(day, part, total_parts)
            body = "\n".join(invert_schedule_blocks(group))

            pages.append(
                SchedulePage(
                    content=f"{header}\n{body}",
                    routine_ids=day.get("routine_ids", set()),
                    date=day.get("date_obj"),
                )
            )

    return pages


def build_notes(skipped_task_ids: list[int], skipped_routines: list[str], frame_cost: int) -> str:
    """The "didn't fit" lines that go below the schedule, in what room is left for them.

    Both lists grow with the user's data, so both are measured against what the
    frame leaves once a page keeps its minimum. Discord refuses an over-long
    message instead of truncating it, and a reader would lose the schedule
    itself over the list of what it left out.
    """
    budget = MESSAGE_LIMIT - frame_cost - MIN_PAGE_CHARS
    notes = ""

    if skipped_task_ids and budget > 0:
        # A runaway list of ids must not crowd the routines out entirely: they
        # are named, and a name says more than a hundredth id does.
        ids_budget = budget // 2 if skipped_routines else budget
        ids = fit_items(
            [str(task_id) for task_id in skipped_task_ids],
            ids_budget - len(TASKS_NOTE.format(ids="")),
            ", ",
            lambda left: f"+{left}",
        )
        if ids:
            notes = TASKS_NOTE.format(ids=ids)

    if skipped_routines and budget - len(notes) > 0:
        heading = ROUTINES_NOTE_HEADING if notes else f"\n{ROUTINES_NOTE_HEADING}"
        routines = fit_items(
            [f"- {routine}" for routine in skipped_routines],
            budget - len(notes) - len(heading),
            overflow=lambda left: f"- ...and {left} more",
        )
        if routines:
            notes += heading + routines

    return notes
