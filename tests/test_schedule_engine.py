"""Tests for what the engine makes of the solver's output before anyone sees it,
and for when it solves at all.

Unlike the other schedule suites this one imports `schedule_engine`, so it needs
the vendored scheduler and `ortools` present.
"""

import asyncio
import datetime
import sqlite3
import sys
import tempfile
import threading
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
from modules import schedule_provider, schedule_stats  # noqa: E402
from modules.schedule_engine import format_skipped_routines, items_from_result  # noqa: E402
from modules.schedule_models import SolvedSchedule, Task  # noqa: E402
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


def forget_every_solve(test: unittest.TestCase):
    """Start the test with no solve kept, none running and no data written."""
    for state in (engine._last_solves, engine._running, schedule_provider._revisions):
        patcher = mock.patch.dict(state, clear=True)
        patcher.start()
        test.addCleanup(patcher.stop)


class TestTheRecordingHook(unittest.IsolatedAsyncioTestCase):
    """Every solve is counted, with everything but the solver itself real.

    Nothing else calls `get_solved_schedule()` — the cog's suite replaces it —
    so without this the hook could stop matching what the engine returns and no
    test would notice.
    """

    def setUp(self):
        forget_every_solve(self)

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
            returned = await engine.get_solved_schedule(4242)

        self.assertEqual(returned.solve_time, 1.25)
        self.assertEqual(schedule_stats.totals(), (1.25, 1))
        self.assertEqual([user for user, _, _ in schedule_stats.top_users()], [4242])

    async def test_a_solve_that_never_ran_leaves_no_trace(self):
        with mock.patch.object(engine, "_solve_sync", return_value=SolvedSchedule(status="NO_DATA")):
            await engine.get_solved_schedule(4242)

        self.assertEqual(schedule_stats.totals(), (0.0, 0))

    async def test_a_schedule_answered_from_the_last_solve_is_not_counted_again(self):
        solved = SolvedSchedule(solve_time=1.25, planning_days=7, status="OPTIMAL")

        with mock.patch.object(engine, "_solve_sync", return_value=solved):
            await engine.get_solved_schedule(4242)
            await engine.get_solved_schedule(4242)

        self.assertEqual(schedule_stats.totals(), (1.25, 1))


USER = 4242
OTHER_USER = 4343


class FakeSolver:
    """Stands in for `_solve_sync`, which the engine runs in a thread.

    Each solve is numbered in its status. `hold()` keeps every solve waiting
    until `let_go()`, which is how a test gets something done while a solve has
    read the data and not yet finished.
    """

    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.released = threading.Event()
        self.released.set()
        self.fail_next: Exception | None = None
        self.on_call = lambda: None

    def __call__(self, client_ID: int) -> SolvedSchedule:
        self.calls += 1
        number = self.calls
        self.on_call()
        self.started.set()
        self.released.wait(5)

        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        return SolvedSchedule(status=f"solve {number}")

    def hold(self):
        self.released.clear()

    def let_go(self):
        self.released.set()

    async def wait_until_started(self):
        if not await asyncio.to_thread(self.started.wait, 5):
            raise AssertionError("The solve never started.")


class TestTheLastSolve(unittest.IsolatedAsyncioTestCase):
    """A schedule is solved again when the data or the clock calls for it, and only then."""

    async def asyncSetUp(self):
        forget_every_solve(self)

        self.solver = FakeSolver()
        self.patch(engine, "_solve_sync", self.solver)
        # A lock belongs to the event loop it first waited on, and every test
        # here runs on a loop of its own.
        self.patch(engine, "_solver_lock", asyncio.Lock())
        # Counting solves is the recording hook's business, and its database is
        # not this suite's.
        self.patch(engine.schedule_stats, "record", mock.Mock())

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.patch(schedule_provider, "get_data_dir", mock.Mock(return_value=Path(directory.name)))
        self.provider = schedule_provider.ScheduleProvider()

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, user_id: int = USER):
        """Change the user's data the way every caller does: through the provider."""
        self.provider.add_task(user_id, Task(name="Read", duration=datetime.timedelta(minutes=30)))

    async def test_unchanged_data_is_answered_from_the_last_solve(self):
        first = await engine.get_solved_schedule(USER)
        second = await engine.get_solved_schedule(USER)

        self.assertEqual(self.solver.calls, 1)
        self.assertIs(second, first)

    async def test_a_write_makes_the_next_call_solve_again(self):
        await engine.get_solved_schedule(USER)
        self.write()

        schedule = await engine.get_solved_schedule(USER)

        self.assertEqual(self.solver.calls, 2)
        self.assertEqual(schedule.status, "solve 2")

    async def test_a_forced_call_solves_although_nothing_changed(self):
        await engine.get_solved_schedule(USER)

        schedule = await engine.get_solved_schedule(USER, force=True)

        self.assertEqual(self.solver.calls, 2)
        self.assertEqual(schedule.status, "solve 2")

    async def test_each_user_has_a_last_solve_of_their_own(self):
        await engine.get_solved_schedule(USER)
        await engine.get_solved_schedule(OTHER_USER)
        self.write(OTHER_USER)
        await engine.get_solved_schedule(USER)

        self.assertEqual(self.solver.calls, 2)

    async def test_callers_at_once_share_one_solve_forced_or_not(self):
        self.solver.hold()
        callers = [
            asyncio.create_task(engine.get_solved_schedule(USER)),
            asyncio.create_task(engine.get_solved_schedule(USER)),
            asyncio.create_task(engine.get_solved_schedule(USER, force=True)),
        ]
        await self.solver.wait_until_started()
        self.solver.let_go()

        schedules = await asyncio.gather(*callers)

        self.assertEqual(self.solver.calls, 1)
        self.assertEqual({schedule.status for schedule in schedules}, {"solve 1"})

    async def test_a_write_during_a_solve_is_not_lost(self):
        # What the Discord message used to do: a change made while the schedule
        # was being solved waited for the next tick, up to ten minutes.
        self.solver.hold()
        caller = asyncio.create_task(engine.get_solved_schedule(USER))
        await self.solver.wait_until_started()

        self.write()
        self.solver.let_go()
        schedule = await caller

        self.assertEqual(self.solver.calls, 2)
        self.assertEqual(schedule.status, "solve 2")

    async def test_data_that_never_stops_changing_still_gets_an_answer(self):
        self.solver.on_call = lambda: schedule_provider._data_changed(USER)

        schedule = await engine.get_solved_schedule(USER)

        self.assertEqual(self.solver.calls, engine.MAX_SOLVES_PER_CALL)
        self.assertEqual(schedule.status, f"solve {engine.MAX_SOLVES_PER_CALL}")

    async def test_a_caller_giving_up_does_not_cancel_the_solve_for_the_others(self):
        self.solver.hold()
        impatient = asyncio.create_task(engine.get_solved_schedule(USER))
        await self.solver.wait_until_started()
        impatient.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await impatient

        patient = asyncio.create_task(engine.get_solved_schedule(USER))
        self.solver.let_go()
        schedule = await patient

        self.assertEqual(self.solver.calls, 1)
        self.assertEqual(schedule.status, "solve 1")

    async def test_a_failed_solve_is_not_kept(self):
        self.solver.fail_next = TimeoutError("CP-SAT solver timed out")
        with self.assertRaises(TimeoutError):
            await engine.get_solved_schedule(USER)

        schedule = await engine.get_solved_schedule(USER)

        self.assertEqual(self.solver.calls, 2)
        self.assertEqual(schedule.status, "solve 2")

    async def test_every_caller_waiting_on_a_failed_solve_hears_of_it(self):
        self.solver.hold()
        self.solver.fail_next = TimeoutError("CP-SAT solver timed out")
        callers = [asyncio.create_task(engine.get_solved_schedule(USER)) for _ in range(2)]
        await self.solver.wait_until_started()
        self.solver.let_go()

        results = await asyncio.gather(*callers, return_exceptions=True)

        self.assertEqual(self.solver.calls, 1)
        self.assertTrue(all(isinstance(result, TimeoutError) for result in results))


if __name__ == "__main__":
    unittest.main()
