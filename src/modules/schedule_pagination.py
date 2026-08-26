"""Cutting one user's schedule into pager-sized pages.

The pager renders a day bottom-up — its last block on top, its first at the
bottom — so the reader's eye ends on the start of the day. That flip is applied
per page, *after* the day has been cut: cutting the already-flipped blocks would
fill page 1 with the evening and leave the morning for page 2.

Pure text handling — no disnake, no settings, no filesystem — so the cog above it
stays Discord-only and this stays unit-testable.
"""

import datetime
from dataclasses import dataclass, field

# A day's blocks are split at this many characters, leaving room for the status
# header and the "didn't fit" notes below it.
PAGE_CHAR_LIMIT = 1500


@dataclass(slots=True)
class SchedulePage:
    """One page: its text, the routines shown on it and the day it covers."""

    content: str
    routine_ids: set = field(default_factory=set)
    date: datetime.date | None = None


def invert_schedule_blocks(blocks: list[str]) -> list[str]:
    """Inverts the chronological order of blocks and lines for a bottom-up view."""
    return ["\n".join(reversed(block.split("\n"))) for block in reversed(blocks)]


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


def paginate_days(days: list[dict], char_limit: int = PAGE_CHAR_LIMIT) -> list[SchedulePage]:
    """One `SchedulePage` per pager page, days and parts alike in chronological order."""
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
