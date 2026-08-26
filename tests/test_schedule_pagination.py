import datetime
import sys
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.schedule_pagination import (  # noqa: E402
    SchedulePage,
    invert_schedule_blocks,
    paginate_days,
)


def block(start: str, name: str, end: str) -> str:
    """A timeline block the way the formatter builds one: start on top, end below."""
    return f"{start}\n ├──> {name}\n{end}"


def make_day(date_str: str = "01.01.2026", weekday: str = "Thursday", blocks: list[str] | None = None, **extra) -> dict:
    return {
        "date_obj": datetime.date(2026, 1, 1),
        "date_str": date_str,
        "weekday": weekday,
        "blocks": blocks if blocks is not None else [],
        "routine_ids": set(),
        **extra,
    }


class TestInvertScheduleBlocks(unittest.TestCase):
    def test_blocks_and_their_lines_are_both_reversed(self):
        blocks = ["08:00\nmorning\n09:00", "19:00\nevening\n20:00"]

        self.assertEqual(
            invert_schedule_blocks(blocks),
            ["20:00\nevening\n19:00", "09:00\nmorning\n08:00"],
        )

    def test_no_blocks_stay_no_blocks(self):
        self.assertEqual(invert_schedule_blocks([]), [])


class TestPaginateDays(unittest.TestCase):
    def test_a_day_that_fits_is_one_page_without_a_part_marker(self):
        day = make_day(blocks=[block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")])

        pages = paginate_days([day])

        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0].content.startswith("=== 01.01.2026 (Thursday) ===\n"))
        self.assertNotIn("Part", pages[0].content)

    def test_a_split_day_opens_on_its_morning(self):
        # The regression this module exists for: splitting the already-flipped
        # blocks used to put the end of the day on page 1 and its start on page 2.
        blocks = [
            block("08:00", "morning", "09:00"),
            block("13:00", "lunch", "14:00"),
            block("19:00", "evening", "20:00"),
        ]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=70)

        self.assertEqual(len(pages), 3)
        self.assertIn("morning", pages[0].content)
        self.assertIn("lunch", pages[1].content)
        self.assertIn("evening", pages[2].content)

    def test_each_page_of_a_split_day_is_still_bottom_up(self):
        blocks = [
            block("08:00", "morning", "09:00"),
            block("10:00", "second", "11:00"),
            block("19:00", "evening", "20:00"),
        ]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=100)

        self.assertEqual(len(pages), 2)
        first_body = pages[0].content.split("\n", 1)[1]
        # Latest block on top, and inside a block the end time above the start.
        self.assertEqual(
            first_body,
            "11:00\n ├──> second\n10:00\n09:00\n ├──> morning\n08:00",
        )

    def test_a_split_day_numbers_its_parts(self):
        blocks = [block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=60)

        self.assertEqual(
            [page.content.splitlines()[0] for page in pages],
            ["=== 01.01.2026 (Thursday) (Part 1/2) ===", "=== 01.01.2026 (Thursday) (Part 2/2) ==="],
        )

    def test_days_keep_their_order_and_each_page_carries_its_day(self):
        first = make_day(date_str="01.01.2026", blocks=[block("08:00", "one", "09:00")])
        second = make_day(date_str="02.01.2026", weekday="Friday", blocks=[block("08:00", "two", "09:00")])
        second["date_obj"] = datetime.date(2026, 1, 2)

        pages = paginate_days([first, second])

        self.assertEqual([page.date for page in pages], [datetime.date(2026, 1, 1), datetime.date(2026, 1, 2)])
        self.assertIn("01.01.2026", pages[0].content)
        self.assertIn("02.01.2026", pages[1].content)

    def test_every_page_of_a_day_carries_that_day_s_routines(self):
        blocks = [block("08:00", "morning", "09:00"), block("19:00", "evening", "20:00")]
        day = make_day(blocks=blocks)
        day["routine_ids"] = {4, 7}

        pages = paginate_days([day], char_limit=60)

        self.assertEqual([page.routine_ids for page in pages], [{4, 7}, {4, 7}])

    def test_a_block_longer_than_the_budget_gets_a_page_of_its_own(self):
        blocks = [block("08:00", "short", "09:00"), block("10:00", "x" * 200, "11:00")]

        pages = paginate_days([make_day(blocks=blocks)], char_limit=80)

        self.assertEqual(len(pages), 2)
        self.assertIn("short", pages[0].content)
        self.assertIn("x" * 200, pages[1].content)

    def test_a_day_without_blocks_produces_no_page(self):
        self.assertEqual(paginate_days([make_day()]), [])

    def test_no_days_produce_no_pages(self):
        self.assertEqual(paginate_days([]), [])

    def test_a_page_defaults_to_no_routines_and_no_date(self):
        page = SchedulePage(content="text")

        self.assertEqual(page.routine_ids, set())
        self.assertIsNone(page.date)


if __name__ == "__main__":
    unittest.main()
