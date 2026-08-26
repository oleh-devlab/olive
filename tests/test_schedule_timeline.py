import datetime
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.schedule_timeline import (  # noqa: E402
    BLOCK_PREFIX,
    GAP_INDENT,
    TASK_PREFIX,
    Columns,
    column_widths,
    format_day_blocks,
    item_id_text,
)

DAY = datetime.date(2026, 8, 26)


@dataclass
class FakeItem:
    """The surface `schedule_timeline` reads off a `ScheduleItem`.

    The real model lives in `schedule_models`, which imports the vendored
    scheduler; mirroring its surface here is what keeps this suite free of the
    submodules.
    """

    item_type: str
    task_name: str
    start: tuple
    end: tuple
    item_id: int | str | None = None
    algo_notes: str = ""
    session_index: str = "1"
    total_sessions: int = 1
    day: datetime.date = field(default=DAY)

    @property
    def dt_start(self) -> datetime.datetime:
        return datetime.datetime.combine(self.day, datetime.time(*self.start))

    @property
    def dt_end(self) -> datetime.datetime:
        return datetime.datetime.combine(self.day, datetime.time(*self.end))

    @property
    def is_task(self) -> bool:
        return self.item_type in ("task", "fixed_routine", "flexible_routine")

    @property
    def tag(self) -> str:
        return {"fixed_routine": "[Fxd Rt.] ", "flexible_routine": "[Flb Rt.] "}.get(self.item_type, "")

    @property
    def duration_min(self) -> int:
        return int((self.dt_end - self.dt_start).total_seconds() // 60)


def task(name="Task", start=(9, 0), end=(10, 0), **extra) -> FakeItem:
    return FakeItem("task", name, start, end, **extra)


def routine(name="Routine", start=(7, 0), end=(7, 30), kind="fixed_routine", **extra) -> FakeItem:
    return FakeItem(kind, name, start, end, **extra)


def content_lines(blocks: list[str]) -> list[str]:
    """The lines that carry an item's text, as opposed to the times around it."""
    return [line for block in blocks for line in block.split("\n") if line.startswith((TASK_PREFIX, BLOCK_PREFIX))]


def text_of(line: str, columns: Columns) -> str:
    """What a line says once its tree prefix and its columns are stepped over."""
    return line[len(TASK_PREFIX) + columns.width :]


class TestItemIdText(unittest.TestCase):
    def test_a_task_shows_its_id(self):
        self.assertEqual(item_id_text(task(item_id=7)), "7")

    def test_an_item_without_an_id_shows_none(self):
        self.assertEqual(item_id_text(task()), "")

    def test_a_named_time_block_shows_its_id(self):
        self.assertEqual(item_id_text(FakeItem("time_block", "Lunch", (13, 0), (14, 0), item_id=3)), "3")

    def test_an_unnamed_time_block_has_nothing_to_label(self):
        self.assertEqual(item_id_text(FakeItem("time_block", "", (13, 0), (14, 0), item_id=3)), "")

    def test_a_gap_shows_no_id(self):
        self.assertEqual(item_id_text(FakeItem("gap", "", (13, 0), (14, 0), item_id=3)), "")


class TestColumnWidths(unittest.TestCase):
    def test_the_widest_id_sets_the_column(self):
        widths = column_widths([task(item_id=7), task(item_id=128), task(item_id=41)])

        self.assertEqual(widths.id_digits, 3)

    def test_a_schedule_without_ids_has_no_id_column(self):
        self.assertEqual(column_widths([task(), task()]).id_field, 0)

    def test_a_schedule_without_routines_has_no_marker_column(self):
        self.assertEqual(column_widths([task(item_id=1)]).tag, 0)

    def test_one_routine_anywhere_gives_every_line_the_column(self):
        self.assertEqual(column_widths([task(item_id=1), routine(item_id=2)]).tag, len("[Fxd Rt.]"))

    def test_nothing_scheduled_measures_nothing(self):
        self.assertEqual(column_widths([]), Columns())


class TestAlignment(unittest.TestCase):
    def setUp(self):
        self.items = [
            routine("Morning run", (7, 0), (7, 30), item_id=3),
            task("Quarterly report", (7, 30), (9, 30), item_id=128),
            FakeItem("time_block", "Lunch", (13, 0), (14, 0), item_id=7),
            routine("Reading", (21, 0), (22, 0), kind="flexible_routine", item_id=41),
        ]
        self.columns = column_widths(self.items)

    def test_every_item_starts_its_text_in_the_same_place(self):
        lines = content_lines(format_day_blocks(self.items, columns=self.columns))

        self.assertEqual(
            [text_of(line, self.columns) for line in lines],
            ["Morning run (30m)", "Quarterly report (120m)", "Lunch (60m)", "Reading (60m)"],
        )

    def test_ids_are_right_aligned_inside_their_brackets(self):
        lines = content_lines(format_day_blocks(self.items, columns=self.columns))

        self.assertIn("[id:  3]", lines[0])
        self.assertIn("[id:128]", lines[1])

    def test_a_task_pays_the_marker_column_in_spaces(self):
        lines = content_lines(format_day_blocks(self.items, columns=self.columns))

        self.assertIn("[id:128]" + " " * (self.columns.tag + 2) + "Quarterly report", lines[1])

    def test_a_gap_stays_out_by_the_trunk(self):
        # It says nothing about an item, so it pays no columns — and reads as a
        # divider rather than as another entry.
        blocks = format_day_blocks(self.items, columns=self.columns)
        gap = next(line for block in blocks for line in block.split("\n") if "break ]" in line)

        self.assertEqual(gap, f"{GAP_INDENT}[ 210m break ]")

    def test_a_solver_note_lines_up_with_the_text(self):
        items = [task("Quarterly report", item_id=128, algo_notes="moved from 08:00")]
        blocks = format_day_blocks(items, columns=column_widths(items))
        note = next(line for line in blocks[0].split("\n") if "!!!" in line)

        self.assertTrue(note.startswith(column_widths(items).indent + "!!!"), note)

    def test_without_columns_the_layout_is_the_bare_tree(self):
        # No columns measured means no padding: the id is dropped with them.
        blocks = format_day_blocks([task("Quarterly report", item_id=128)])

        self.assertIn(f"{TASK_PREFIX}Quarterly report (60m)", blocks[0])


class TestBlockContents(unittest.TestCase):
    def test_a_block_carries_its_start_and_end_time(self):
        blocks = format_day_blocks([task("Report", (9, 0), (10, 30))])

        self.assertEqual(blocks[0].split("\n")[0], "09:00")
        self.assertEqual(blocks[0].split("\n")[-1], "10:30")

    def test_a_shared_boundary_is_not_printed_twice(self):
        blocks = format_day_blocks([task("One", (9, 0), (10, 0)), task("Two", (10, 0), (11, 0))])

        self.assertFalse(blocks[1].startswith("10:00"))

    def test_a_gap_between_items_is_measured(self):
        blocks = format_day_blocks([task("One", (9, 0), (10, 0)), task("Two", (11, 30), (12, 0))])

        self.assertIn("[ 90m break ]", blocks[1])

    def test_an_unnamed_time_block_is_left_out(self):
        items = [FakeItem("time_block", "", (13, 0), (14, 0), item_id=3)]

        self.assertEqual(format_day_blocks(items), [])

    def test_a_split_task_says_which_session_this_is(self):
        blocks = format_day_blocks([task("Report", total_sessions=3, session_index="2")])

        self.assertIn("[s. 2/3]", blocks[0])

    def test_a_spillover_says_it_came_from_yesterday(self):
        yesterday = task("Report", (23, 0), (23, 59), item_id=5)
        blocks = format_day_blocks([], spillovers=[yesterday], columns=column_widths([yesterday]))

        self.assertIn("[From yesterday] Report", blocks[0])

    def test_a_gap_item_falls_back_to_its_note(self):
        blocks = format_day_blocks([FakeItem("gap", "", (9, 0), (9, 30), algo_notes="idle")])

        self.assertIn("idle (30m)", blocks[0])

    def test_a_gap_item_without_a_note_is_just_a_break(self):
        blocks = format_day_blocks([FakeItem("gap", "", (9, 0), (9, 30))])

        self.assertIn("Break (30m)", blocks[0])


if __name__ == "__main__":
    unittest.main()
