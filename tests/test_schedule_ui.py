"""Tests for the schedule message the cog builds: its pages, the buttons under
them, and when it asks the engine for a new solve.

This suite drives the cog, so it needs `disnake`, the vendored scheduler and
`ortools` — `cogs.schedule.ui` reaches the engine through its own imports.
"""

import datetime
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

# The cog imports the operator's `settings` module, absent from a checkout. The
# loop's error handler takes `channels` from it at import.
settings = sys.modules.setdefault("settings", types.ModuleType("settings"))
if not hasattr(settings, "channels"):
    settings.channels = {}

import cogs.schedule.loop as loop  # noqa: E402
import cogs.schedule.ui as ui  # noqa: E402
from core.paged_message import BLANK_LABEL, PaginationView  # noqa: E402
from modules.schedule_models import ScheduleItem, SolvedSchedule  # noqa: E402

DAY = datetime.date(2026, 8, 26)


def at(day_offset: int, hour: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY + datetime.timedelta(days=day_offset), datetime.time(hour))


def item(kind: str, name: str, day_offset: int, hour: int, item_id: int) -> ScheduleItem:
    return ScheduleItem(
        item_type=kind,
        task_name=name,
        dt_start=at(day_offset, hour),
        dt_end=at(day_offset, hour + 1),
        session_index="1",
        total_sessions=1,
        algo_notes="",
        item_id=item_id,
    )


async def build(items: list[ScheduleItem], update_seconds: float | None = None, **solved_fields):
    """The pages the cog would publish for this solve."""
    source = ui.SchedulePageSource(update_interval=lambda: update_seconds)
    source.phrases = lambda guild_id: {}
    solved = SolvedSchedule(items=items, solve_time=0.1, planning_days=7, status="OPTIMAL", **solved_fields)

    with mock.patch.object(ui, "get_solved_schedule", return_value=solved):
        pages = await source.build_pages(1, None)

    return source, pages


def buttons_of(source, page) -> list:
    return source.extra_components(page, 0, None)


class TestSkipButtons(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Two days: the first holds two routines, the second holds one.
        self.items = [
            item("fixed_routine", "Зарядка", 0, 7, 3),
            item("flexible_routine", "Англійська", 0, 20, 41),
            item("task", "Звіт", 0, 9, 128),
            item("fixed_routine", "Зарядка", 1, 7, 3),
            item("task", "Пошта", 1, 9, 129),
        ]

    async def test_every_page_draws_the_same_number_of_buttons(self):
        source, pages = await build(self.items)

        counts = {len(buttons_of(source, page)) for page in pages}

        self.assertEqual(len(pages), 2)
        self.assertEqual(counts, {3})  # the label plus the busiest day's two slots

    async def test_a_thinner_day_pays_the_difference_in_blanks(self):
        source, pages = await build(self.items)

        second_day = [button.label for button in buttons_of(source, pages[1])]

        self.assertEqual(second_day, ["Skip rout.:", "ID 3", BLANK_LABEL])

    async def test_the_busiest_day_spends_every_slot_on_a_routine(self):
        source, pages = await build(self.items)

        first_day = [button.label for button in buttons_of(source, pages[0])]

        self.assertEqual(first_day, ["Skip rout.:", "ID 3", "ID 41"])

    async def test_a_schedule_without_routines_draws_no_skip_block(self):
        source, pages = await build([item("task", "Звіт", 0, 9, 128)])

        self.assertEqual(buttons_of(source, pages[0]), [])

    async def test_nothing_scheduled_at_all_draws_no_skip_block(self):
        source, pages = await build([])

        self.assertEqual(len(pages), 1)
        self.assertEqual(buttons_of(source, pages[0]), [])

    async def test_a_skip_button_carries_the_routine_and_the_day(self):
        source, pages = await build(self.items)

        ids = [button.custom_id for button in buttons_of(source, pages[1])]

        self.assertIn(f"{ui.SKIP_PREFIX}3_{(DAY + datetime.timedelta(days=1)).isoformat()}", ids)

    async def test_a_blank_cannot_be_pressed_and_is_not_a_skip(self):
        source, pages = await build(self.items)

        blank = buttons_of(source, pages[1])[-1]

        self.assertTrue(blank.disabled)
        self.assertTrue(blank.custom_id.startswith(ui.BLANK_PREFIX))
        self.assertFalse(blank.custom_id.startswith(ui.SKIP_PREFIX))

    async def test_more_routines_than_the_view_holds_are_capped(self):
        crowded = [item("fixed_routine", f"Рутина {number}", 0, 6, number) for number in range(30)]

        source, pages = await build(crowded)
        view = PaginationView.for_source(source, extra=buttons_of(source, pages[0]))

        self.assertEqual(len(view.children), PaginationView.MAX_COMPONENTS)
        self.assertEqual([len(row["components"]) for row in view.to_components()], [5, 5, 5, 5, 5])


class TestAutoUpdates(unittest.IsolatedAsyncioTestCase):
    """How often the message says it updates is how often the loop really runs."""

    async def test_the_page_says_how_often_the_loop_solves_again(self):
        _, pages = await build([item("task", "Report", 0, 10, 1)], update_seconds=600.0)
        self.assertIn("Auto-updates every 10 min", pages[0].content)

    async def test_without_the_loop_the_page_says_it_does_not_update_on_its_own(self):
        _, pages = await build([item("task", "Report", 0, 10, 1)], update_seconds=None)
        self.assertIn("Auto-updates every N/A min", pages[0].content)

    def test_the_interval_is_read_off_the_running_loop(self):
        bot = mock.Mock()
        bot.get_cog.return_value = mock.Mock(main_loop=mock.Mock(hours=0, minutes=10, seconds=30))
        self.assertEqual(loop.update_interval(bot), 630)
        bot.get_cog.assert_called_with(loop.ScheduleMessageLoop.__cog_name__)

    def test_without_the_loop_loaded_nothing_solves_again_on_its_own(self):
        bot = mock.Mock()
        bot.get_cog.return_value = None
        self.assertIsNone(loop.update_interval(bot))

    def test_the_cog_asks_the_loop_each_time_a_page_is_drawn(self):
        bot = mock.Mock()
        bot.get_cog.return_value = None
        source = ui.ScheduleUI(bot).source
        self.assertIsNone(source.update_interval())
        bot.get_cog.return_value = mock.Mock(main_loop=mock.Mock(hours=None, minutes=None, seconds=900.0))
        self.assertEqual(source.update_interval(), 900.0)


if __name__ == "__main__":
    unittest.main()


class TestWhenItSolves(unittest.IsolatedAsyncioTestCase):
    """A data change may be answered from the last solve; a tick or the refresh button may not."""

    async def test_a_forced_build_asks_the_engine_for_a_new_solve(self):
        source = ui.SchedulePageSource()
        source.phrases = lambda guild_id: {}

        with mock.patch.object(ui, "get_solved_schedule", return_value=SolvedSchedule()) as solve:
            await source.build_pages(1, None)
            await source.build_pages(1, None, force=True)

        self.assertEqual(solve.call_args_list, [mock.call(1, force=False), mock.call(1, force=True)])

    async def asyncSetUp(self):
        self.controller = mock.AsyncMock()
        patcher = mock.patch.dict(ui.cache.channel_states, {555: self.controller})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cog = ui.ScheduleUI(mock.Mock())

    async def test_a_data_change_rebuilds_without_forcing(self):
        await self.cog.handle_schedule_update(555)

        self.controller.refresh.assert_awaited_once_with(rebuild=True, force=False)

    async def test_a_tick_forces_a_new_solve(self):
        await self.cog.handle_schedule_tick(555)

        self.controller.refresh.assert_awaited_once_with(rebuild=True, force=True)

    async def test_the_loop_ticks_with_the_event_that_forces(self):
        listened = {name for name, _ in ui.ScheduleUI.__cog_listeners__}

        self.assertEqual(loop.ScheduleMessageLoop.update_event, "schedule_tick")
        self.assertIn("on_schedule_tick", listened)

    async def test_the_page_says_when_the_schedule_was_solved_not_when_it_was_shown(self):
        solved_at = datetime.datetime(2026, 8, 26, 9, 30, 15)

        _, pages = await build([item("task", "Report", 0, 10, 1)], computed_at=solved_at)

        self.assertIn("26.08.2026 09:30:15", pages[0].content)
