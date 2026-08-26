"""Tests for what the engine makes of the solver's output before anyone sees it.

Unlike the other schedule suites this one imports `schedule_engine`, so it needs
the vendored scheduler and `ortools` present.
"""

import datetime
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The engine imports the operator's `settings` module, absent from a checkout.
# Stubbed here rather than relying on another test module having done it first.
sys.modules.setdefault("settings", types.ModuleType("settings"))

import modules.schedule_engine as engine  # noqa: E402
from database.migrations import MigrationRunner  # noqa: E402
from modules import schedule_stats  # noqa: E402
from modules.schedule_engine import format_skipped_routines, items_from_result  # noqa: E402
from modules.schedule_models import SolvedSchedule  # noqa: E402
from tests.test_schedule_stats import FakeDatabase  # noqa: E402


class FakeTask:
    """One day's copy of a routine, the way the expander hands it to the solver."""

    def __init__(self, routine_id=None, deadline=None, name="Routine", id=None):
        self.routine_id, self.deadline, self.name = routine_id, deadline, name
        self.id = id if id is not None else (f"r_{routine_id}_2026-08-26" if routine_id else None)


class FakeSkipped:
    """What the solver hands back for something it could not place."""

    def __init__(self, task):
        self.task = task


def deadline(day: int, hour: int = 23, minute: int = 59) -> datetime.datetime:
    return datetime.datetime(2026, 8, day, hour, minute)


class FakeSolved:
    """A `ScheduledTask` / `ScheduledRoutine`: something placed, with its times."""

    def __init__(self, task, start, end, chunks=(), **extra):
        self.task, self.start_time, self.end_time, self.chunks = task, start, end, list(chunks)
        self.__dict__.update(extra)


class FakeBlock:
    def __init__(self, name, start, end, id=None):
        self.name, self.start_time, self.end_time, self.id = name, start, end, id


class FakeResult:
    def __init__(self, tasks=(), routines=(), blocks=()):
        self.scheduled_tasks, self.scheduled_routines, self.scheduled_timeblocks = (
            list(tasks),
            list(routines),
            list(blocks),
        )


def when(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 8, 26, hour, minute)


class TestItemsFromResult(unittest.TestCase):
    def test_a_task_placed_whole_is_one_item(self):
        result = FakeResult(tasks=[FakeSolved(FakeTask(name="Звіт"), when(9), when(10))])

        items = items_from_result(result)

        self.assertEqual([(i.item_type, i.task_name, i.total_sessions) for i in items], [("task", "Звіт", 1)])
        self.assertEqual(items[0].session_index, "1")

    def test_a_task_split_into_sessions_is_one_item_each(self):
        chunks = [FakeSolved(None, when(9), when(10)), FakeSolved(None, when(14), when(15))]
        result = FakeResult(tasks=[FakeSolved(FakeTask(name="Звіт"), when(9), when(15), chunks=chunks)])

        items = items_from_result(result)

        self.assertEqual([(i.session_index, i.total_sessions) for i in items], [("1", 2), ("2", 2)])
        self.assertEqual([(i.dt_start, i.dt_end) for i in items], [(when(9), when(10)), (when(14), when(15))])

    def test_a_routine_is_typed_by_how_it_is_scheduled(self):
        routines = [
            FakeSolved(FakeTask(name="Зарядка"), when(7), when(8), routine_type="fixed", routine_id=3),
            FakeSolved(FakeTask(name="Читання"), when(21), when(22), routine_type="flexible", routine_id=5),
        ]

        items = items_from_result(FakeResult(routines=routines))

        self.assertEqual([(i.item_type, i.item_id) for i in items], [("fixed_routine", 3), ("flexible_routine", 5)])

    def test_a_time_block_keeps_its_own_id(self):
        items = items_from_result(FakeResult(blocks=[FakeBlock("Обід", when(13), when(14), id=7)]))

        self.assertEqual([(i.item_type, i.task_name, i.item_id) for i in items], [("time_block", "Обід", 7)])

    def test_everything_placed_comes_back_in_one_chronological_list(self):
        result = FakeResult(
            tasks=[FakeSolved(FakeTask(name="Звіт"), when(9), when(10))],
            routines=[FakeSolved(FakeTask(name="Зарядка"), when(7), when(8), routine_type="fixed", routine_id=3)],
            blocks=[FakeBlock("Обід", when(13), when(14))],
        )

        self.assertEqual([i.task_name for i in items_from_result(result)], ["Зарядка", "Звіт", "Обід"])

    def test_an_empty_solve_places_nothing(self):
        self.assertEqual(items_from_result(FakeResult()), [])


class TestFormatSkippedRoutines(unittest.TestCase):
    def test_a_routine_is_named_once_with_the_days_it_missed(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(day), "Вчити англійську")) for day in (26, 27, 28)]

        self.assertEqual(format_skipped_routines(skipped), ["Вчити англійську: 26.08, 27.08, 28.08"])

    def test_a_routine_is_named_rather_than_numbered(self):
        # The id groups the days; what the reader is shown is the name.
        skipped = [FakeSkipped(FakeTask(23, deadline(26), "Ранкова зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Ранкова зарядка: 26.08"])

    def test_a_name_that_holds_a_parenthetical_keeps_it(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(26), "Англійська (з репетитором)"))]

        self.assertEqual(format_skipped_routines(skipped), ["Англійська (з репетитором): 26.08"])

    def test_the_day_is_named_without_the_clock_time(self):
        # A routine runs at most once a day, so the time said nothing.
        skipped = [FakeSkipped(FakeTask(5, deadline(26, 9, 15), "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08"])

    def test_a_day_is_not_listed_twice(self):
        skipped = [FakeSkipped(FakeTask(5, deadline(26), "Зарядка")), FakeSkipped(FakeTask(5, deadline(26), "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08"])

    def test_past_the_threshold_the_days_collapse_into_a_count(self):
        skipped = [FakeSkipped(FakeTask(23, deadline(day), "Зарядка")) for day in range(20, 25)]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка (missed 5 times)"])

    def test_each_routine_gets_its_own_entry(self):
        skipped = [
            FakeSkipped(FakeTask(23, deadline(26), "Зарядка")),
            FakeSkipped(FakeTask(7, deadline(27), "Читання")),
        ]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08", "Читання: 27.08"])

    def test_two_routines_sharing_a_name_stay_apart(self):
        skipped = [
            FakeSkipped(FakeTask(23, deadline(26), "Зарядка")),
            FakeSkipped(FakeTask(7, deadline(27), "Зарядка")),
        ]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: 26.08", "Зарядка: 27.08"])

    def test_a_routine_without_a_deadline_says_so(self):
        skipped = [FakeSkipped(FakeTask(9, None, "Зарядка"))]

        self.assertEqual(format_skipped_routines(skipped), ["Зарядка: no deadline"])

    def test_a_routine_with_no_id_of_its_own_groups_by_name(self):
        skipped = [FakeSkipped(FakeTask(None, deadline(day), "Вчити англійську")) for day in (26, 27)]

        self.assertEqual(format_skipped_routines(skipped), ["Вчити англійську: 26.08, 27.08"])

    def test_nothing_skipped_says_nothing(self):
        self.assertEqual(format_skipped_routines([]), [])


class TestTheRecordingHook(unittest.IsolatedAsyncioTestCase):
    """Every solve is counted, with everything but the solver itself real.

    Nothing else calls `solve_schedule()` — the cog's suite replaces it — so
    without this the hook could stop matching what the engine returns and no
    test would notice.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)

        connection = sqlite3.connect(Path(directory.name) / "test.sqlite3")
        self.addCleanup(connection.close)
        MigrationRunner(connection).migrate()

        schedule_stats.use(FakeDatabase(connection))
        self.addCleanup(schedule_stats.use, None)

    async def test_a_solve_is_recorded_under_the_user_it_was_for(self):
        solved = SolvedSchedule(solve_time=1.25, planning_days=7, status="OPTIMAL")

        with mock.patch.object(engine, "_solve_sync", return_value=solved):
            returned = await engine.solve_schedule(4242)

        self.assertEqual(returned.solve_time, 1.25)
        self.assertEqual(schedule_stats.totals(), (1.25, 1))
        self.assertEqual([user for user, _, _ in schedule_stats.top_users()], [4242])

    async def test_a_solve_that_never_ran_leaves_no_trace(self):
        with mock.patch.object(engine, "_solve_sync", return_value=SolvedSchedule(status="NO_DATA")):
            await engine.solve_schedule(4242)

        self.assertEqual(schedule_stats.totals(), (0.0, 0))


if __name__ == "__main__":
    unittest.main()
