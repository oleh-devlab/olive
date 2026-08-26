"""Rendering solver items into the timeline blocks a day is made of.

A block is a small drawing: the start time on top, the content in the middle,
the end time below. What makes a column of them readable is a constant left
edge, so the id and the routine marker are padded to a width measured over the
*whole* schedule rather than one day or one page — a column that shifted when
the reader turned the page would be worse than no column at all.

Pure text handling — no disnake, no settings, no solver — so this stays
unit-testable while `schedule_formatter` above it talks to the engine.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.schedule_models import ScheduleItem

TASK_PREFIX = " ├──> "
BLOCK_PREFIX = " ├──- "
BRANCH = " │"

# A gap says nothing about an item, so it stays out by the trunk instead of
# paying the columns in spaces — which also keeps it scannable as "nothing here".
GAP_INDENT = " │    "
# The gap carries the time the day resumes, which saves the line that used to
# print it on its own. What it does not carry is the previous item's end time:
# that stays with its own block, which a page break may put on the page before.
GAP_TEXT = "[ {mins}m break → {resumes} ]"

ID_FIELD = "[id:{id}]"
SPILL_MARKER = "[From yesterday] "
BREAK_TEXT = "Break"


@dataclass(frozen=True, slots=True)
class Columns:
    """What every line spends before its text, so all text starts in one place."""

    id_digits: int = 0
    tag: int = 0

    @property
    def id_field(self) -> int:
        """Width of `[id:12]`, or 0 when nothing in the schedule carries an id."""
        return self.id_digits + len(ID_FIELD.format(id="")) if self.id_digits else 0

    @property
    def width(self) -> int:
        return sum(field + 1 for field in (self.id_field, self.tag) if field)

    @property
    def indent(self) -> str:
        """Where a block's own lines — a gap, a solver note — line up with its text."""
        return BRANCH + " " * (len(TASK_PREFIX) + self.width - len(BRANCH))


def item_id_text(item: "ScheduleItem") -> str:
    """The id an item shows in the schedule, or "" when it shows none."""
    if item.item_id is None:
        return ""

    if item.item_type == "time_block":
        # An unnamed block is not drawn at all, so its id has nothing to label.
        return str(item.item_id) if item.task_name else ""

    return str(item.item_id) if item.is_task else ""


def column_widths(items: list["ScheduleItem"]) -> Columns:
    """Measure the id and routine-marker columns over every item in the schedule."""
    return Columns(
        id_digits=max((len(item_id_text(item)) for item in items), default=0),
        tag=max((len(item.tag.strip()) for item in items), default=0),
    )


def _columns(item: "ScheduleItem", columns: Columns) -> str:
    """One item's id and marker fields, padded to the schedule's columns.

    The digits are right-aligned inside the brackets so the brackets themselves
    hold still; an item without an id pays the same width in spaces.
    """
    fields = []

    if columns.id_field:
        id_text = item_id_text(item)
        fields.append(ID_FIELD.format(id=id_text.rjust(columns.id_digits)) if id_text else " " * columns.id_field)

    if columns.tag:
        fields.append(item.tag.strip().ljust(columns.tag))

    return "".join(f"{field} " for field in fields)


def _item_text(item: "ScheduleItem", is_spill: bool) -> tuple[str, str]:
    """An item's prefix and the text after its columns."""
    marker = SPILL_MARKER if is_spill else ""

    if item.is_task:
        text = f"{marker}{item.task_name} ({item.duration_min}m)"
        if item.total_sessions > 1:
            text += f" [s. {item.session_index}/{item.total_sessions}]"

        return TASK_PREFIX, text

    if item.item_type == "time_block":
        return BLOCK_PREFIX, f"{marker}{item.task_name or BREAK_TEXT} ({item.duration_min}m)"

    return BLOCK_PREFIX, f"{marker}{item.algo_notes or BREAK_TEXT} ({item.duration_min}m)"


def format_day_blocks(
    items: list["ScheduleItem"],
    spillovers: list["ScheduleItem"] | None = None,
    columns: Columns | None = None,
) -> list[str]:
    """Format items into visual timeline blocks, in chronological order (start on top, end on bottom)."""
    columns = columns or Columns()
    spillovers = spillovers or []
    blocks = []
    last_end = None

    for item in [*spillovers, *items]:
        # Unnamed timeblocks are intentionally skipped in the visual representation.
        # This causes them to coalesce with adjacent algorithmic gaps into a single `[ Xm break ]` indicator.
        if item.item_type == "time_block" and not item.task_name:
            continue

        lines = []

        # The start time at the top of the block — or, after a gap, the gap line
        # carrying it. A shared boundary with the previous block needs neither:
        # its end time is already there.
        if last_end is None or last_end != item.dt_start:
            if last_end and item.dt_start > last_end:
                gap_mins = int((item.dt_start - last_end).total_seconds() / 60)
                lines.append(GAP_INDENT + GAP_TEXT.format(mins=gap_mins, resumes=item.dt_start.strftime("%H:%M")))
            else:
                lines.append(item.dt_start.strftime("%H:%M"))

        prefix, text = _item_text(item, item in spillovers)

        # A gap's text *is* its note, so only the other two kinds print one.
        if item.algo_notes and (item.is_task or item.item_type == "time_block"):
            lines.append(f"{columns.indent}!!! {item.algo_notes}")

        lines.append(f"{prefix}{_columns(item, columns)}{text}")

        # End time at the bottom of the block
        lines.append(item.dt_end.strftime("%H:%M"))

        blocks.append("\n".join(lines))
        last_end = item.dt_end

    return blocks
