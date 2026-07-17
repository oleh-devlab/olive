import disnake
from disnake.ext import commands, tasks

import core.cache as cache
from core.utils import format_embed_data, get_phrases
import settings

from modules.schedule_provider import ScheduleProvider

UPDATE_SECONDS = getattr(settings, "usage_stats_update_seconds", 30)


class UsageStatsEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.provider = ScheduleProvider()
        self.update_usage_stats.start()

    def cog_unload(self):
        self.update_usage_stats.cancel()

    @tasks.loop(seconds=UPDATE_SECONDS)
    async def update_usage_stats(self):
        # Calculate schedule users
        try:
            channels_data = self.provider.load_channels()
            schedule_users = len(channels_data)
        except Exception:
            schedule_users = 0

        # Calculate LLM consented users
        llm_consented = 0
        if hasattr(cache, "llm_consent") and cache.llm_consent:
            llm_consented = cache.llm_consent.get_consented_users_count()

        raw_embed_data = (
            get_phrases()
            .get("usage_stats_embed", {})
            .get(
                "embed_data",
                {
                    "title": ":chart_with_upwards_trend: | Usage Statistics",
                    "description": "Schedule users: `{schedule_users}`\nLLM consented: `{llm_consented}`",
                },
            )
        )

        formatted_embed_data = format_embed_data(
            raw_embed_data, schedule_users=schedule_users, llm_consented=llm_consented
        )

        embed = disnake.Embed.from_dict(formatted_embed_data)

        footer_text = (
            get_phrases()
            .get("utils", {})
            .get("update_interval", "Updates every {seconds} seconds.")
            .format(seconds=UPDATE_SECONDS)
        )
        embed.set_footer(text=footer_text)

        cache.embeds_to_send["usage_stats"] = embed


def setup(bot: commands.Bot) -> None:
    bot.add_cog(UsageStatsEmbed(bot))
