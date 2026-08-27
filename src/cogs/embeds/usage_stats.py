import logging
from typing import ClassVar

from disnake.ext import commands

from core import cache
from core.embed_cog import BaseEmbedCog
from modules import schedule_stats
from modules.inflation_provider import inflation_provider
from modules.schedule_provider import channels_registry as schedule_channels

logger = logging.getLogger(__name__)


class UsageStatsEmbed(BaseEmbedCog):
    embed_key = "usage_stats"
    phrases_section = "usage_stats_embed"
    settings_key = "usage_stats_update_seconds"
    default_seconds = 30
    fallback_embed: ClassVar[dict] = {
        "title": ":chart_with_upwards_trend: | Usage Statistics",
        "description": (
            "Schedule users: `{schedule_users}`\n"
            "Inflation users: `{inflation_users}`\n"
            "Inflation channels: `{inflation_channels}`\n"
            "LLM consented: `{llm_consented}`\n\n"
            "**Schedule solver** *(since {solve_since})*\n"
            "Today: `{solve_today}` | 7 days: `{solve_week}` | 30 days: `{solve_month}`\n"
            "All time: `{solve_total}` over `{solve_count}` solves"
        ),
    }

    async def get_data(self):
        # Calculate schedule users
        try:
            schedule_users = schedule_channels.count()
        except Exception:
            schedule_users = 0

        # Inflation is counted twice on purpose: the commands work without a
        # channel, so "users" is everyone with records, "channels" only those
        # who set up the report channel.
        try:
            inflation_users = inflation_provider.count_users_with_records()
            inflation_channels = inflation_provider.count_users_with_channels()
        except Exception as e:
            logger.error(f"Error counting inflation users: {e}")
            inflation_users = 0
            inflation_channels = 0

        # Calculate LLM consented users
        llm_consented = 0
        if hasattr(cache, "llm_consent_manager") and cache.llm_consent_manager:
            llm_consented = cache.llm_consent_manager.get_consented_users_count()

        return {
            "schedule_users": schedule_users,
            "inflation_users": inflation_users,
            "inflation_channels": inflation_channels,
            "llm_consented": llm_consented,
            **self.solver_stats(),
        }

    def solver_stats(self) -> dict:
        """What the CP-SAT solver has cost, by window and by user."""
        try:
            total_seconds, total_solves = schedule_stats.totals()

            return {
                "solve_today": schedule_stats.format_duration(schedule_stats.totals(days=1)[0]),
                "solve_week": schedule_stats.format_duration(schedule_stats.totals(days=7)[0]),
                "solve_month": schedule_stats.format_duration(schedule_stats.totals(days=30)[0]),
                "solve_total": schedule_stats.format_duration(total_seconds),
                "solve_count": total_solves,
                "solve_since": schedule_stats.format_day(schedule_stats.counting_since()),
            }
        except Exception as e:
            logger.error(f"Error reading schedule solve stats: {e}")

            return dict.fromkeys(("solve_today", "solve_week", "solve_month", "solve_total", "solve_since"), "N/A") | {
                "solve_count": 0
            }


def setup(bot: commands.Bot) -> None:
    bot.add_cog(UsageStatsEmbed(bot))
