import datetime
import sys
import unittest
from pathlib import Path

# Setup path so we can import from src
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from modules.schedule_pagination import (  # noqa: E402
    MESSAGE_LIMIT,
    MIN_PAGE_CHARS,
    SchedulePage,
    build_notes,
    fit_items,
    invert_schedule_blocks,
    page_char_limit,
    paginate_days,
    trim_to_whole_lines,
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

        pages = paginate_days([day], char_limit=200)

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

        pages = paginate_days([first, second], char_limit=200)

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
        self.assertEqual(paginate_days([make_day()], char_limit=200), [])

    def test_no_days_produce_no_pages(self):
        self.assertEqual(paginate_days([], char_limit=200), [])

    def test_a_page_defaults_to_no_routines_and_no_date(self):
        page = SchedulePage(content="text")

        self.assertEqual(page.routine_ids, set())
        self.assertIsNone(page.date)


class TestPageCharLimit(unittest.TestCase):
    def test_a_page_gets_what_the_frame_leaves(self):
        self.assertEqual(page_char_limit(500), MESSAGE_LIMIT - 500)

    def test_an_over_long_frame_cannot_starve_the_page(self):
        # The frame is trimmed instead — a page of nothing is not worth turning to.
        self.assertEqual(page_char_limit(MESSAGE_LIMIT + 100), MIN_PAGE_CHARS)


class TestFitItems(unittest.TestCase):
    def test_everything_that_fits_is_kept_whole(self):
        self.assertEqual(fit_items(["one", "two"], 100, ", "), "one, two")

    def test_the_overflow_note_says_what_was_cut(self):
        self.assertEqual(fit_items(["1", "2", "3", "4"], 8, ", ", lambda left: f"+{left}"), "1, 2, +2")

    def test_the_note_is_paid_for_out_of_the_budget(self):
        packed = fit_items([str(number) for number in range(50)], 20, ", ", lambda left: f"+{left}")

        self.assertLessEqual(len(packed), 20)
        self.assertTrue(packed.endswith(("+45", "+46", "+47")), packed)

    def test_a_budget_that_fits_nothing_leaves_the_note_alone(self):
        self.assertEqual(fit_items(["a very long item"], 6, ", ", lambda left: f"+{left}"), "+1")

    def test_a_budget_that_fits_not_even_the_note_gives_up(self):
        self.assertEqual(fit_items(["a very long item"], 1, ", ", lambda left: f"+{left}"), "")

    def test_no_items_produce_no_text(self):
        self.assertEqual(fit_items([], 100, ", ", lambda left: f"+{left}"), "")


class TestBuildNotes(unittest.TestCase):
    def test_nothing_skipped_says_nothing(self):
        self.assertEqual(build_notes([], [], 400), "")

    def test_both_lists_are_named(self):
        notes = build_notes([7, 9], ["Morning run"], 400)

        self.assertIn("7, 9", notes)
        self.assertIn("- Morning run", notes)

    def test_the_notes_stay_inside_what_the_frame_leaves(self):
        frame_cost = 400

        notes = build_notes(list(range(400)), [f"Routine {number}" for number in range(80)], frame_cost)

        self.assertLessEqual(frame_cost + len(notes) + MIN_PAGE_CHARS, MESSAGE_LIMIT)

    def test_a_runaway_id_list_still_leaves_room_for_the_routines(self):
        notes = build_notes(list(range(400)), ["Morning run", "Reading"], 400)

        self.assertIn("- Morning run", notes)
        self.assertIn("- Reading", notes)

    def test_a_frame_that_leaves_no_room_drops_the_notes(self):
        self.assertEqual(build_notes([1, 2], ["Morning run"], MESSAGE_LIMIT), "")


class TestTrimToWholeLines(unittest.TestCase):
    def test_text_within_the_limit_is_untouched(self):
        self.assertEqual(trim_to_whole_lines("one\ntwo", 100), "one\ntwo")

    def test_trimming_stops_on_a_line_boundary(self):
        self.assertEqual(trim_to_whole_lines("one\ntwo\nthree", 9), "one\ntwo")

    def test_a_single_over_long_line_is_cut_mid_line(self):
        self.assertEqual(trim_to_whole_lines("x" * 40, 10), "x" * 10)


if __name__ == "__main__":
    unittest.main()
