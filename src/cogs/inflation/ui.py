import logging
from datetime import datetime

import disnake
import settings
from disnake.ext import commands

from core import cache
from core.paged_message import Page, PageSource, PaginationView, ensure_controller
from core.time_utils import tz
from core.utils import format_phrase, get_phrases
from modules.inflation_report import build_report, build_server_report, render_page

logger = logging.getLogger(__name__)


class InflationPageSource(PageSource):
    """
    Pages of one user's inflation report.

    Building a report is cheap (no solver), so there is no "is calculating"
    guard here — the pager itself only rebuilds on refresh anyway.
    """

    # Both strings are load-bearing: `message_type` finds the existing message in
    # `webhooks_config.json`, `view_prefix` is the buttons' custom_id prefix.
    message_type = "inflation"
    view_prefix = "inflation"
    phrases_section = "inflation"

    def phrases(self, guild_id: int | None) -> dict:
        return get_phrases(guild_id).get(self.phrases_section, {})

    async def build_pages(self, user_id: int, guild_id: int | None) -> list[Page]:
        summary, record_pages = build_report(user_id, guild_id)

        # With no records `render_page` still has something to say (the summary
        # plus a hint), so a single page is returned rather than none.
        if not record_pages:
            return [Page(content=render_page(summary, [], 0, guild_id))]

        return [Page(content=render_page(summary, record_pages, index, guild_id)) for index in range(len(record_pages))]

    def welcome_text(self, guild_id: int | None) -> str:
        return self.phrases(guild_id).get("welcome_message", "Initializing the inflation report...")

    def error_page(self, guild_id: int | None, error: Exception) -> Page:
        text = format_phrase(self.phrases(guild_id), "report_error", "Failed to build the report: {error}", error=error)

        return Page(content=text)

    def header(self, guild_id: int | None) -> str:
        update_seconds = getattr(settings, "inflation_loop_update_seconds", 3600)

        return format_phrase(
            self.phrases(guild_id),
            "updated_header",
            "`{formatted_time}` *(auto-updates every {update_mins} min)*",
            formatted_time=datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S"),
            update_mins=max(update_seconds // 60, 1),
        )


class InflationServerPageSource(InflationPageSource):
    """
    The guild's shared budget, as a single page with no pager.

    Everything the reader sees is inherited from the personal source — the same
    welcome text, header and error page — because it is the same report; only
    the trimming differs, and that lives in `build_server_report`.
    """

    # A different message so it gets its own entry in `webhooks_config.json`, and
    # a different prefix so a stray personal pager button cannot drive it.
    message_type = "inflation_server"
    view_prefix = "inflation_server"
    paginated = False

    async def build_pages(self, owner_id: int, guild_id: int | None) -> list[Page]:
        # The header is prepended to the page content, so it eats into the same
        # message limit the report has to fit in. +2 for the blank line after it.
        reserve = len(self.header(guild_id)) + 2

        return [Page(content=build_server_report(owner_id, reserve))]


class InflationUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.source = InflationPageSource()
        self.server_source = InflationServerPageSource()

    async def cog_load(self):
        # Persistent view: the buttons keep working across restarts.
        self.bot.add_view(PaginationView.for_source(InflationPageSource))

    @commands.Cog.listener("on_inflation_update")
    async def handle_inflation_update(self, channel_id: int):
        controller = cache.channel_states.get(channel_id)
        if not controller:
            logger.debug("No controller for channel %s yet; skipping update.", channel_id)
            return

        await controller.refresh(rebuild=True)

    @commands.Cog.listener("on_inflation_init")
    async def handle_inflation_init(self, channel: disnake.TextChannel, user_id: int):
        await ensure_controller(self.bot, channel.id, user_id, self.source)

    @commands.Cog.listener("on_inflation_server_update")
    async def handle_inflation_server_update(self, channel_id: int):
        await self.handle_inflation_update(channel_id)

    @commands.Cog.listener("on_inflation_server_init")
    async def handle_inflation_server_init(self, channel: disnake.TextChannel, guild_id: int):
        # The "owner" of a server report is the guild itself, so its id is what
        # the source gets asked to build pages for.
        await ensure_controller(self.bot, channel.id, guild_id, self.server_source)


def setup(bot):
    bot.add_cog(InflationUI(bot))
