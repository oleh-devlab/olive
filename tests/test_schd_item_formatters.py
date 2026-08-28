"""Tests for the listings a person and the agent read.

The module imports nothing, so neither does this suite.
"""

import datetime
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.schd_item_formatters import (  # noqa: E402
    DISCORD,
    PLAIN,
    Style,
    format_completed_task_list,
    format_routine_info,
    format_routine_list,
    format_task_info,
    format_task_list,
    format_timeblock_list,
)


def minutes(count: int) -> datetime.timedelta:
    return datetime.timedelta(minutes=count)


@dataclass
class FakeTask:
    id: int = 5
    name: str = "Написати звіт"
    description: str = "Про квартал"
    duration: datetime.timedelta = datetime.timedelta(minutes=120)
    priority: int = 3
    deadline: datetime.datetime | None = datetime.datetime(2026, 9, 1, 18, 0)
    max_chunk_duration: datetime.timedelta | None = datetime.timedelta(minutes=45)
    min_chunk_duration: datetime.timedelta | None = None
    break_duration: datetime.timedelta = datetime.timedelta(minutes=10)
    depends_on: list = field(default_factory=list)


@dataclass
class FakeBlock:
    id: int = 7
    name: str | None = "Обід"
    start: datetime.time = datetime.time(13, 0)
    end: datetime.time = datetime.time(14, 0)
    daily: bool = True
    weekdays: list | None = None


@dataclass
class FakeRoutine:
    id: int = 23
    name: str = "Англійська"
    type: str = "flexible"
    repeat: str = "weekly"
    duration: datetime.timedelta = datetime.timedelta(minutes=45)
    break_duration: datetime.timedelta = datetime.timedelta(minutes=5)
    priority: int = 2
    time: datetime.time | None = None
    deadline_time: datetime.time | None = datetime.time(21, 30)
    weekdays: list | None = field(default_factory=lambda: [0, 2, 4])
    depends_on: list = field(default_factory=list)
    resume_after: datetime.date | None = None


class TestStyle(unittest.TestCase):
    def test_plain_text_wears_no_markup(self):
        self.assertEqual(PLAIN.b("Name"), "Name")
        self.assertEqual(PLAIN.c("[ID: 5]"), "[ID: 5]")

    def test_discord_wears_markdown(self):
        self.assertEqual(DISCORD.b("Name"), "**Name**")
        self.assertEqual(DISCORD.c("[ID: 5]"), "`[ID: 5]`")

    def test_only_a_headed_style_opens_with_one(self):
        self.assertEqual(DISCORD.heading("Your Tasks:"), ["**Your Tasks:**"])
        self.assertEqual(PLAIN.heading("Your Tasks:"), [])

    def test_a_title_is_dressed_for_a_reader_and_stated_for_the_agent(self):
        self.assertEqual(DISCORD.title("Task Details (ID: 5)", "ID: 5"), "**Task Details (ID: 5)**")
        self.assertEqual(PLAIN.title("Task Details (ID: 5)", "ID: 5"), "ID: 5")

    def test_a_separator_puts_the_fields_on_one_line(self):
        pairs = [("Session", "45 min"), ("Break", "10 min")]

        self.assertEqual(DISCORD.fields(pairs), ["**Session:** 45 min  |  **Break:** 10 min"])

    def test_without_one_each_field_keeps_its_own_line(self):
        pairs = [("Session", "45 min"), ("Break", "10 min")]

        self.assertEqual(PLAIN.fields(pairs), ["Session: 45 min", "Break: 10 min"])

    def test_a_style_can_be_dressed_without_being_headed(self):
        # The two are separate: markup is not the same decision as structure.
        style = Style(bold="__")

        self.assertEqual(style.b("Name"), "__Name__")
        self.assertEqual(style.heading("Your Tasks:"), [])


class TestTaskList(unittest.TestCase):
    def test_nothing_to_list_says_so_in_either_style(self):
        self.assertEqual(format_task_list([], PLAIN), "No tasks found.")
        self.assertEqual(format_task_list([], DISCORD), "No tasks found.")

    def test_the_agent_gets_one_line_per_task_and_no_heading(self):
        self.assertEqual(
            format_task_list([FakeTask()], PLAIN),
            "[ID: 5] Написати звіт - 120 min (Priority: 3)",
        )

    def test_discord_gets_a_heading_over_the_same_lines(self):
        lines = format_task_list([FakeTask()], DISCORD).split("\n")

        self.assertEqual(lines[0], "**Your Tasks:**")
        self.assertEqual(lines[1], "`[ID: 5]` **Написати звіт** - 120 min (Priority: 3)")

    def test_dependencies_are_named_when_there_are_any(self):
        listed = format_task_list([FakeTask(depends_on=[1, 2])], PLAIN)

        self.assertTrue(listed.endswith("(Priority: 3) (Depends on: 1, 2)"), listed)

    def test_a_completed_task_is_listed_without_its_duration(self):
        self.assertEqual(format_completed_task_list([FakeTask()], PLAIN), "[ID: 5] Написати звіт (Priority: 3)")

    def test_no_completed_tasks_says_so(self):
        self.assertEqual(format_completed_task_list([], PLAIN), "No completed tasks found in history.")


class TestTaskInfo(unittest.TestCase):
    def test_a_missing_task_says_so(self):
        self.assertEqual(format_task_info(None, PLAIN), "Task not found.")

    def test_the_agent_is_told_the_id_the_reader_sees_in_a_title(self):
        self.assertEqual(format_task_info(FakeTask(), PLAIN).split("\n")[0], "ID: 5")
        self.assertEqual(format_task_info(FakeTask(), DISCORD).split("\n")[0], "**Task Details (ID: 5)**")

    def test_session_and_break_share_a_line_only_in_discord(self):
        self.assertIn("**Session:** 45 min  |  **Break:** 10 min", format_task_info(FakeTask(), DISCORD))

        plain = format_task_info(FakeTask(), PLAIN).split("\n")
        self.assertIn("Session: 45 min", plain)
        self.assertIn("Break: 10 min", plain)

    def test_an_empty_description_reads_as_none(self):
        self.assertIn("Description: (none)", format_task_info(FakeTask(description="   "), PLAIN))

    def test_a_task_without_a_deadline_says_none(self):
        self.assertIn("Deadline: none", format_task_info(FakeTask(deadline=None), PLAIN))

    def test_a_task_that_cannot_be_chunked_reports_no_session(self):
        self.assertIn("Session: N/A min", format_task_info(FakeTask(max_chunk_duration=None), PLAIN))

    def test_a_shortenable_session_is_mentioned_only_when_set(self):
        self.assertIn(
            "Min session shortening allowed: 20 min", format_task_info(FakeTask(min_chunk_duration=minutes(20)), PLAIN)
        )
        self.assertNotIn("Min session", format_task_info(FakeTask(), PLAIN))


class TestTimeBlockList(unittest.TestCase):
    def test_nothing_to_list_says_so(self):
        self.assertEqual(format_timeblock_list([], PLAIN), "No time blocks found.")

    def test_a_block_reports_its_hours_and_how_often(self):
        self.assertEqual(format_timeblock_list([FakeBlock()], PLAIN), "[ID: 7] Обід 13:00 - 14:00 (Daily)")

    def test_a_one_time_block_says_so(self):
        self.assertIn("(One-time)", format_timeblock_list([FakeBlock(daily=False)], PLAIN))

    def test_a_weekly_block_names_its_days(self):
        block = FakeBlock(daily=False, weekdays=[0, 2, 4])
        self.assertIn("(Weekly on [0, 2, 4])", format_timeblock_list([block], PLAIN))

    def test_an_unnamed_block_is_listed_by_its_hours_alone(self):
        self.assertEqual(format_timeblock_list([FakeBlock(name=None)], PLAIN), "[ID: 7] 13:00 - 14:00 (Daily)")

    def test_hours_that_cannot_be_read_show_as_question_marks(self):
        self.assertIn("??? - ???", format_timeblock_list([FakeBlock(start=None, end=None)], PLAIN))

    def test_a_block_that_raises_is_reported_rather_than_losing_the_listing(self):
        class BrokenBlock:
            id = 9

            @property
            def start(self):
                raise ValueError("bad data")

        listed = format_timeblock_list([BrokenBlock(), FakeBlock()], PLAIN).split("\n")

        self.assertEqual(listed[0], "[ID: 9] Invalid Block Data")
        self.assertIn("Обід", listed[1])


class TestRoutineList(unittest.TestCase):
    def test_nothing_to_list_says_so(self):
        self.assertEqual(format_routine_list([], PLAIN), "No routines found.")

    def test_a_flexible_routine_is_listed_by_its_deadline(self):
        self.assertEqual(
            format_routine_list([FakeRoutine()], PLAIN),
            "[ID: 23] Англійська (flexible, weekly on [0, 2, 4], 45m) by 21:30",
        )

    def test_a_fixed_routine_is_listed_by_the_hour_it_starts(self):
        routine = FakeRoutine(type="fixed", repeat="daily", time=datetime.time(7, 0), weekdays=None)

        self.assertEqual(format_routine_list([routine], PLAIN), "[ID: 23] Англійська (fixed, daily, 45m) @ 07:00")

    def test_a_skipped_routine_says_when_it_comes_back(self):
        routine = FakeRoutine(resume_after=datetime.date(2026, 9, 3))

        self.assertIn("[Resumes after 03.09.2026]", format_routine_list([routine], PLAIN))


class TestRoutineInfo(unittest.TestCase):
    def test_a_missing_routine_says_so(self):
        self.assertEqual(format_routine_info(None, PLAIN), "Routine not found.")

    def test_a_weekly_routine_names_its_weekdays(self):
        self.assertIn("Weekdays: [0, 2, 4] (0=Mon, 6=Sun)", format_routine_info(FakeRoutine(), PLAIN))

    def test_a_flexible_routine_reports_a_deadline_and_a_fixed_one_a_time(self):
        self.assertIn("Deadline: 21:30", format_routine_info(FakeRoutine(), PLAIN))

        fixed = FakeRoutine(type="fixed", repeat="daily", time=datetime.time(7, 0), weekdays=None)
        self.assertIn("Time: 07:00", format_routine_info(fixed, PLAIN))

    def test_dependencies_and_a_skip_are_mentioned_only_when_set(self):
        routine = FakeRoutine(depends_on=[7], resume_after=datetime.date(2026, 9, 3))
        detailed = format_routine_info(routine, PLAIN)

        self.assertIn("Depends On: 7", detailed)
        self.assertIn("Resumes after: 03.09.2026", detailed)
        self.assertNotIn("Depends On", format_routine_info(FakeRoutine(), PLAIN))


if __name__ == "__main__":
    unittest.main()
