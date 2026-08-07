import json
import subprocess

from disnake.ext import commands
from settings import is_battery, max_safe_percent_charge, min_safe_percent_charge

from core.embed_cog import BaseEmbedCog

min_perc = min_safe_percent_charge
max_perc = max_safe_percent_charge
HOURS_PER_PERCENT = 0.95


class Battery(BaseEmbedCog):
    embed_key = "battery"
    phrases_section = "battery_embed"
    phrases_key = "battery_embed"
    settings_key = "battery_update_seconds"
    default_seconds = 180
    fallback_embed = {"title": ":battery: | Battery Information", "description": "Error with getting text."}

    def should_start(self) -> bool:
        # raw_embed = get_phrases().get("battery_embed", {}).get("no_battery_embed", {"title": ":battery: | No battery information available", "description": "This device does not have battery information or it cannot be accessed."})
        # core.cache.embeds_to_send["battery"] = disnake.Embed.from_dict(raw_embed)
        return bool(is_battery)

    async def get_data(self):
        """
        Cyclic update of the battery information embed from Termux
        """

        result = subprocess.run(["termux-battery-status"], capture_output=True, text=True)
        if result.returncode != 0:
            print("Error occurred while fetching battery information")
            return None

        battery_info = json.loads(result.stdout)
        health = battery_info.get("health", "N/A")
        percentage = battery_info.get("percentage", 0)
        plugged = battery_info.get("plugged", "N/A")
        status = battery_info.get("status", "N/A")
        temperature = battery_info.get("temperature", 0.0)
        current = battery_info.get("current", 0)

        safe_battery_percent = (
            ((percentage - min_perc) / (max_perc - min_perc)) * 100
            if min_perc <= percentage <= max_perc
            else (100 if percentage >= max_perc else 0)
        )
        time_to_end = (percentage - min_perc) * HOURS_PER_PERCENT if percentage >= min_perc else 0

        return {
            "health": health,
            "percentage": percentage,
            "plugged": plugged,
            "status": status,
            "temperature": temperature,
            "current": current,
            "safe_battery_percent": safe_battery_percent,
            "time_to_end": time_to_end,
            "plus_sign": "+" if percentage > max_perc else "",
        }


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Battery(bot))
