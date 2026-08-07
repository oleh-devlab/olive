from datetime import datetime

import settings
from disnake.ext import commands
from settings import is_battery

from core.embed_cog import BaseEmbedCog
from core.time_utils import tz


class UptimeEmbed(BaseEmbedCog):
    embed_key = "uptime"
    phrases_section = "uptime_embed"
    settings_key = "uptime_update_seconds"
    default_seconds = 30
    fallback_embed = {"title": ":clock1: | Uptime", "description": "`{uptime_str}\n{cost_str}`"}

    def __init__(self, bot):
        self.watt = 0.6
        self.start_time = datetime.now(tz)  # Approximate bot start time

        super().__init__(bot)

    async def get_data(self):
        """
        Report the current uptime and the estimated cost based on power consumption.
        """
        delta = datetime.now(tz) - self.start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        uptime_str = (
            f"{days} дн. {hours} год. {minutes} хв. {seconds} сек."
            if days > 0
            else f"{hours} год. {minutes} хв. {seconds} сек."
        )

        cost_kwh = getattr(settings, "cost_kwh", 4.32) if is_battery else 0
        uptime_all_hours = delta.total_seconds() / 3600
        cost_session = (self.watt / 1000) * uptime_all_hours * cost_kwh

        cost_str = f"{cost_session:.4f}{'' if is_battery else '(VPS)'} uah."

        return {"uptime_str": uptime_str, "cost_str": cost_str}


def setup(bot: commands.Bot) -> None:
    bot.add_cog(UptimeEmbed(bot))
