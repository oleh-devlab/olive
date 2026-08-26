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

TRUNK = " ├"
# The arrowhead's shaft, which is where a routine's marker goes: a marker in a
# column of its own sat level with the task names and blurred into them.
SHAFT = "──"
TASK_ARROW = ">"
BLOCK_ARROW = "-"
ROUTINE_MARKERS = {"fixed_routine": "[Fxd]", "flexible_routine": "[Flb]"}
BRANCH = " │"

# A gap rides on the end time it follows rather than taking a line of its own.
# The line it does not take is the next item's start time: what a reader looks
# for first is where the next thing begins, so that keeps a bare line to itself.
GAP_TEXT = "[ {mins}m break ]"

ID_FIELD = "[id:{id}]"
SPILL_MARKER = "[From yesterday] "
BREAK_TEXT = "Break"


@dataclass(frozen=True, slots=True)
class Columns:
    """What every line spends before its text, so all text starts in one place."""

    id_digits: int = 0
    marker: int = 0

    @property
    def id_field(self) -> int:
        """Width of `[id:12]`, or 0 when nothing in the schedule carries an id."""
        return self.id_digits + len(ID_FIELD.format(id="")) if self.id_digits else 0

    @property
    def prefix(self) -> int:
        """Width of the tree prefix, which is what carries the routine marker."""
        return len(TRUNK) + len(SHAFT) + self.marker + len(TASK_ARROW) + 1

    @property
    def width(self) -> int:
        """Everything an item spends before its text: the prefix and the id field."""
        return self.prefix + (self.id_field + 1 if self.id_field else 0)

    @property
    def indent(self) -> str:
        """Where a block's own lines — a gap, a solver note — line up with its text."""
        return BRANCH + " " * (self.width - len(BRANCH))


def item_id_text(item: "ScheduleItem") -> str:
    """The id an item shows in the schedule, or "" when it shows none."""
    if item.item_id is None:
        return ""

    if item.item_type == "time_block":
        # An unnamed block is not drawn at all, so its id has nothing to label.
        return str(item.item_id) if item.task_name else ""

    return str(item.item_id) if item.is_task else ""


def routine_marker(item: "ScheduleItem") -> str:
    """`[Fxd]` or `[Flb]` for a routine, "" for anything else."""
    return ROUTINE_MARKERS.get(item.item_type, "")


def column_widths(items: list["ScheduleItem"]) -> Columns:
    """Measure the id and routine-marker columns over every item in the schedule."""
    return Columns(
        id_digits=max((len(item_id_text(item)) for item in items), default=0),
        marker=max((len(routine_marker(item)) for item in items), default=0),
    )


def _prefix(item: "ScheduleItem", columns: Columns) -> str:
    """The branch an item hangs off, with its marker set into the shaft.

    An item that is not a routine pays the marker's width in shaft, so every
    arrowhead — and everything after it — lands in the same place.
    """
    marker = routine_marker(item) if columns.marker else ""
    arrow = TASK_ARROW if item.is_task else BLOCK_ARROW

    return f"{TRUNK}{SHAFT}{marker or '─' * columns.marker}{arrow} "


def _id_field(item: "ScheduleItem", columns: Columns) -> str:
    """One item's id field, padded to the schedule's column.

    The digits are right-aligned inside the brackets so the brackets themselves
    hold still; an item without an id pays the same width in spaces.
    """
    if not columns.id_field:
        return ""

    id_text = item_id_text(item)
    field = ID_FIELD.format(id=id_text.rjust(columns.id_digits)) if id_text else " " * columns.id_field

    return f"{field} "


def _item_text(item: "ScheduleItem", is_spill: bool) -> str:
    """What an item says after its columns."""
    marker = SPILL_MARKER if is_spill else ""

    if item.is_task:
        text = f"{marker}{item.task_name} ({item.duration_min}m)"
        if item.total_sessions > 1:
            text += f" [s. {item.session_index}/{item.total_sessions}]"

        return text

    if item.item_type == "time_block":
        return f"{marker}{item.task_name or BREAK_TEXT} ({item.duration_min}m)"

    return f"{marker}{item.algo_notes or BREAK_TEXT} ({item.duration_min}m)"


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

        # The start time at the top of the block. A shared boundary with the
        # previous block needs none: its end time is already there.
        if last_end is None or last_end != item.dt_start:
            if last_end and item.dt_start > last_end:
                # Hung off the end time that opened the gap, which is the last
                # line of the previous block — `last_end` says there is one.
                gap_mins = int((item.dt_start - last_end).total_seconds() / 60)
                blocks[-1] += f" {GAP_TEXT.format(mins=gap_mins)}"

            lines.append(item.dt_start.strftime("%H:%M"))

        # A gap's text *is* its note, so only the other two kinds print one.
        if item.algo_notes and (item.is_task or item.item_type == "time_block"):
            lines.append(f"{columns.indent}!!! {item.algo_notes}")

        lines.append(f"{_prefix(item, columns)}{_id_field(item, columns)}{_item_text(item, item in spillovers)}")

        # End time at the bottom of the block
        lines.append(item.dt_end.strftime("%H:%M"))

        blocks.append("\n".join(lines))
        last_end = item.dt_end

    return blocks
