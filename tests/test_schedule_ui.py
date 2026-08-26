"""Tests for the buttons under the schedule, which the cog builds per page.

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

# The cog imports the operator's `settings` module, absent from a checkout.
sys.modules.setdefault("settings", types.ModuleType("settings"))

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


async def build(items: list[ScheduleItem]):
    """The pages the cog would publish for this solve."""
    source = ui.SchedulePageSource()
    source.phrases = lambda guild_id: {}
    solved = SolvedSchedule(items=items, solve_time=0.1, planning_days=7, status="OPTIMAL")

    with mock.patch.object(ui, "solve_schedule", return_value=solved) as solve:
        solve.return_value = solved
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


if __name__ == "__main__":
    unittest.main()
