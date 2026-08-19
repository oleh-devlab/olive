import logging
from datetime import datetime

import disnake
import settings
from disnake.ext import commands

from core import cache
from core.paged_message import Page, PageSource, PaginationView, ensure_controller
from core.time_utils import tz
from core.utils import format_phrase, get_phrases
from modules.inflation_report import build_report, render_page

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


class InflationUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.source = InflationPageSource()

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


def setup(bot):
    bot.add_cog(InflationUI(bot))
