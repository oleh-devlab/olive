from typing import ClassVar

from disnake.ext import commands

from core import cache
from core.embed_cog import BaseEmbedCog
from modules.schedule_provider import ScheduleProvider


class UsageStatsEmbed(BaseEmbedCog):
    embed_key = "usage_stats"
    phrases_section = "usage_stats_embed"
    settings_key = "usage_stats_update_seconds"
    default_seconds = 30
    fallback_embed: ClassVar[dict] = {
        "title": ":chart_with_upwards_trend: | Usage Statistics",
        "description": "Schedule users: `{schedule_users}`\nLLM consented: `{llm_consented}`",
    }

    def __init__(self, bot: commands.Bot) -> None:
        self.provider = ScheduleProvider()

        super().__init__(bot)

    async def get_data(self):
        # Calculate schedule users
        try:
            channels_data = self.provider.load_channels()
            schedule_users = len(channels_data)
        except Exception:
            schedule_users = 0

        # Calculate LLM consented users
        llm_consented = 0
        if hasattr(cache, "llm_consent_manager") and cache.llm_consent_manager:
            llm_consented = cache.llm_consent_manager.get_consented_users_count()

        return {"schedule_users": schedule_users, "llm_consented": llm_consented}


def setup(bot: commands.Bot) -> None:
    bot.add_cog(UsageStatsEmbed(bot))
