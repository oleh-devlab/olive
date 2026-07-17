import disnake
from disnake.ext import commands, tasks
import subprocess
import json

from settings import is_battery, battery_update_seconds, min_safe_percent_charge, max_safe_percent_charge

import core.cache
import core.utils
from core.utils import get_phrases

min_perc = min_safe_percent_charge
max_perc = max_safe_percent_charge
HOURS_PER_PERCENT = 0.95


class Battery(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if is_battery:
            self.battery_loop.start()
        else:
            pass
            # raw_embed = get_phrases().get("battery_embed", {}).get("no_battery_embed", {"title": ":battery: | No battery information available", "description": "This device does not have battery information or it cannot be accessed."})
            # core.cache.embeds_to_send["battery"] = disnake.Embed.from_dict(raw_embed)

    def cog_unload(self):
        self.battery_loop.cancel()

    @tasks.loop(seconds=battery_update_seconds)
    async def battery_loop(self):
        """
        Cyclic update of the battery information embed from Termux
        """

        result = subprocess.run(["termux-battery-status"], capture_output=True, text=True)
        if result.returncode == 0:
            battery_info = json.loads(result.stdout)
            health = battery_info.get("health", "N/A")
            percentage = battery_info.get("percentage", 0)
            plugged = battery_info.get("plugged", "N/A")
            status = battery_info.get("status", "N/A")
            temperature = battery_info.get("temperature", 0.0)
            current = battery_info.get("current", 0)
        else:
            print("Error occurred while fetching battery information")
            return

        safe_battery_percent = (
            ((percentage - min_perc) / (max_perc - min_perc)) * 100
            if min_perc <= percentage <= max_perc
            else (100 if percentage >= max_perc else 0)
        )
        plus_percent = percentage > max_perc
        time_to_end = (percentage - min_perc) * HOURS_PER_PERCENT if percentage >= min_perc else 0

        plus_sign = "+" if plus_percent else ""

        raw_embed = (
            get_phrases()
            .get("battery_embed", {})
            .get("battery_embed", {"title": ":battery: | Battery Information", "description": "Error with getting text."})
        )

        embed = disnake.Embed.from_dict(
            core.utils.format_embed_data(
                raw_embed,
                health=health,
                percentage=percentage,
                plugged=plugged,
                status=status,
                temperature=temperature,
                current=current,
                safe_battery_percent=safe_battery_percent,
                time_to_end=time_to_end,
                plus_sign=plus_sign,
            )
        )

        footer_text = (
            get_phrases()
            .get("utils", {})
            .get("update_interval", "Updates every {seconds} seconds.")
            .format(seconds=battery_update_seconds)
        )
        embed.set_footer(text=footer_text)

        core.cache.embeds_to_send["battery"] = embed


def setup(bot):
    bot.add_cog(Battery(bot))
