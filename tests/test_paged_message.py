import sys
import unittest
from pathlib import Path

import disnake

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from core.paged_message import (  # noqa: E402
    BLANK_LABEL,
    MAX_EMBED_CHARS_PER_MESSAGE,
    MAX_EMBEDS_PER_MESSAGE,
    Page,
    PageSource,
    PaginationView,
    PagedChannelMessage,
    blank_buttons,
    chunk_embeds,
)


def make_embed(description: str = "x") -> disnake.Embed:
    return disnake.Embed(description=description)


class TestPage(unittest.TestCase):
    def test_kwargs_always_carry_both_keys(self):
        # An edit that omits `embeds` keeps the previous page's embeds on screen,
        # so a text-only page has to send an explicit empty list.
        kwargs = Page(content="hello").to_kwargs()

        self.assertEqual(kwargs["content"], "hello")
        self.assertEqual(kwargs["embeds"], [])

    def test_empty_page_sends_empty_content(self):
        self.assertEqual(Page().to_kwargs(), {"content": "", "embeds": []})

    def test_fingerprint_tracks_content_and_embeds(self):
        page = Page(content="a", embeds=[make_embed("one")])

        self.assertEqual(page.fingerprint(), Page(content="a", embeds=[make_embed("one")]).fingerprint())
        self.assertNotEqual(page.fingerprint(), Page(content="b", embeds=[make_embed("one")]).fingerprint())
        self.assertNotEqual(page.fingerprint(), Page(content="a", embeds=[make_embed("two")]).fingerprint())
        self.assertNotEqual(page.fingerprint(), Page(content="a").fingerprint())


class HeaderSource(PageSource):
    message_type = "test"
    view_prefix = "test"

    def header(self, guild_id):
        return "HEADER"

    def extra_components(self, page, page_index, guild_id):
        return [
            disnake.ui.Button(label=str(routine_id), custom_id=f"test_extra_{routine_id}")
            for routine_id in page.meta.get("routine_ids", [])
        ]


class TestPagePayload(unittest.IsolatedAsyncioTestCase):
    async def test_header_does_not_cost_the_page_its_meta(self):
        # The buttons of a page are built from its meta; wrapping the content in
        # a header used to hand extra_components() a page without any.
        controller = PagedChannelMessage.__new__(PagedChannelMessage)
        controller.source = HeaderSource()
        controller.current_page = 0
        controller.pages = [Page(content="body", meta={"routine_ids": [7, 9]})]

        page = controller._payload(0, None)

        self.assertTrue(page.content.startswith("HEADER"))
        self.assertIn("body", page.content)
        self.assertEqual(page.meta, {"routine_ids": [7, 9]})
        self.assertEqual(
            [item.custom_id for item in controller._build_view(page, None).children[5:]],
            ["test_extra_7", "test_extra_9"],
        )


class TestUnpaginatedSource(unittest.IsolatedAsyncioTestCase):
    """A source that always builds one page is published without a pager."""

    def controller(self, paginated: bool) -> PagedChannelMessage:
        class Source(PageSource):
            view_prefix = "test"

        Source.paginated = paginated

        controller = PagedChannelMessage.__new__(PagedChannelMessage)
        controller.source = Source()
        controller.current_page = 0
        controller.pages = [Page(content="body")]

        return controller

    async def test_no_view_is_built(self):
        self.assertIsNone(self.controller(paginated=False)._build_view(Page(), None))
        self.assertIsNotNone(self.controller(paginated=True)._build_view(Page(), None))

    async def test_the_view_key_is_left_out_entirely(self):
        # `webhook.send()` — the path that recreates a deleted message — rejects
        # view=None, so the key must be absent rather than None.
        payload = PagedChannelMessage._with_view({"content": "body"}, None)

        self.assertNotIn("view", payload)

    async def test_a_view_is_still_attached_when_there_is_one(self):
        view = PaginationView("test")

        payload = PagedChannelMessage._with_view({"content": "body"}, view)

        self.assertIs(payload["view"], view)


class TestBlankButtons(unittest.IsolatedAsyncioTestCase):
    async def test_a_blank_takes_a_slot_and_says_nothing(self):
        blanks = blank_buttons(3, "pad_")

        self.assertEqual(len(blanks), 3)
        self.assertTrue(all(button.label == BLANK_LABEL for button in blanks))
        self.assertTrue(all(button.disabled for button in blanks))

    async def test_every_blank_has_its_own_custom_id(self):
        ids = [button.custom_id for button in blank_buttons(4, "pad_")]

        self.assertEqual(ids, ["pad_0", "pad_1", "pad_2", "pad_3"])
        self.assertEqual(len(set(ids)), len(ids))

    async def test_nothing_to_pad_adds_nothing(self):
        self.assertEqual(blank_buttons(0, "pad_"), [])

    async def test_a_full_house_of_extras_is_not_dropped(self):
        # Five pager buttons plus twenty extras is exactly Discord's 25.
        view = PaginationView("test", extra=blank_buttons(20, "pad_"))

        self.assertEqual(len(view.children), PaginationView.MAX_COMPONENTS)
        self.assertEqual([len(row["components"]) for row in view.to_components()], [5, 5, 5, 5, 5])


class TestPaginationView(unittest.IsolatedAsyncioTestCase):
    async def test_for_source_takes_the_prefix_and_the_phrases_section(self):
        class Source(PageSource):
            view_prefix = "sched"
            phrases_section = "schedule"

        view = PaginationView.for_source(Source())

        self.assertEqual(view.children[0].custom_id, "sched_first_page")
        self.assertEqual(view.children[0].phrases_section, "schedule")

    async def test_phrases_section_defaults_to_the_prefix(self):
        view = PaginationView("test")

        self.assertEqual(view.children[0].phrases_section, "test")

    async def test_extra_components_are_added(self):
        extra = [disnake.ui.Button(label="x", custom_id="test_extra")]

        view = PaginationView("test", extra=extra)

        self.assertEqual(len(view.children), len(PaginationView.BUTTONS) + 1)

    async def test_too_many_extras_are_dropped_instead_of_raising(self):
        # Discord allows 25 components; going over used to raise inside the
        # render and leave the message frozen for good.
        extra = [disnake.ui.Button(label=str(i), custom_id=f"test_{i}") for i in range(40)]

        view = PaginationView("test", extra=extra)

        self.assertEqual(len(view.children), PaginationView.MAX_COMPONENTS)


class TestChunkEmbeds(unittest.TestCase):
    def test_no_embeds(self):
        self.assertEqual(chunk_embeds([]), [])

    def test_fits_into_one_page(self):
        embeds = [make_embed() for _ in range(MAX_EMBEDS_PER_MESSAGE)]

        self.assertEqual(len(chunk_embeds(embeds)), 1)

    def test_respects_the_embed_count_limit(self):
        embeds = [make_embed() for _ in range(MAX_EMBEDS_PER_MESSAGE * 2 + 1)]

        pages = chunk_embeds(embeds)

        self.assertEqual(len(pages), 3)
        self.assertEqual([len(page) for page in pages], [MAX_EMBEDS_PER_MESSAGE, MAX_EMBEDS_PER_MESSAGE, 1])

    def test_respects_the_character_limit(self):
        # Ten of these are under the count limit but way over 6000 characters.
        embeds = [make_embed("x" * 1000) for _ in range(MAX_EMBEDS_PER_MESSAGE)]

        pages = chunk_embeds(embeds)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(sum(len(embed) for embed in page), MAX_EMBED_CHARS_PER_MESSAGE)

    def test_oversized_embed_gets_its_own_page(self):
        embeds = [make_embed("a"), make_embed("b" * (MAX_EMBED_CHARS_PER_MESSAGE + 100)), make_embed("c")]

        pages = chunk_embeds(embeds)

        self.assertEqual(len(pages), 3)
        self.assertEqual(len(pages[1]), 1)

    def test_every_embed_survives_chunking(self):
        embeds = [make_embed(f"embed {i}") for i in range(25)]

        chunked = [embed for page in chunk_embeds(embeds) for embed in page]

        self.assertEqual(chunked, embeds)


if __name__ == "__main__":
    unittest.main()
