import asyncio
from datetime import datetime
import disnake
from disnake.ext import commands, tasks


from settings import is_battery

import core.cache
from core.utils import format_embed_data, get_phrases

from core.time_utils import tz

class UptimeEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.j = True

        self.watt = 0.6

        self.start_time = datetime.now(tz) # Approximate bot start time

        self.update_uptime.start()


    def cog_unload(self):
        self.update_uptime.cancel()
    
    @tasks.loop(seconds=30)
    async def update_uptime(self):
        """
        Update the uptime embed with the current uptime and estimated cost based on power consumption.
        """

        if self.j:
            await asyncio.sleep(75)
            self.j = False

        now = datetime.now(tz)
        delta = now - self.start_time
        days = delta.days
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        uptime_str = (
            f"{days} дн. {hours} год. {minutes} хв. {seconds} сек."
            if days > 0 else
            f"{hours} год. {minutes} хв. {seconds} сек."
        )
        
        if is_battery:
            cost_kwh = 4.32
        else:
            cost_kwh = 0

        uptime_all_hours = delta.total_seconds() / 3600
        
        cost_session = (self.watt/1000)*uptime_all_hours*cost_kwh
        
        cost_str = f"{cost_session:.4f}{'' if is_battery else '(VPS)'} uah."

        raw_embed_data = get_phrases().get("uptime_embed", {}).get("embed_data", { "title": "Uptime", "description": "{uptime_str}" })
        formatted_embed_data = format_embed_data(raw_embed_data, uptime_str=uptime_str, cost_str=cost_str)
        embed = disnake.Embed.from_dict(formatted_embed_data)

        core.cache.embeds_to_send["uptime"] = embed

def setup(bot):
    bot.add_cog(UptimeEmbed(bot))