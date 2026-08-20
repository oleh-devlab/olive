from typing import ClassVar

import psutil
from disnake.ext import commands

from core.embed_cog import BaseEmbedCog


async def get_memory_info():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "memory_total_gib": round(mem.total / (1024**3), 2),
        "memory_used_gib": round(mem.used / (1024**3), 2),
        "memory_percent": mem.percent,
        "swap_total_gib": round(swap.total / (1024**3), 2),
        "swap_used_gib": round(swap.used / (1024**3), 2),
        "swap_percent": swap.percent,
    }


class Hosting(BaseEmbedCog):
    embed_key = "server_load"
    phrases_section = "hosting_embed"
    phrases_key = "server_embed_data"
    settings_key = "hosting_update_seconds"
    default_seconds = 10
    fallback_embed: ClassVar[dict] = {"title": ":nut_and_bolt: | Server"}

    async def get_data(self):
        memory_info = await get_memory_info()

        total_used = memory_info["swap_used_gib"] + memory_info["memory_used_gib"]
        total_total = memory_info["swap_total_gib"] + memory_info["memory_total_gib"]
        total_percent = (100 * (total_used / total_total)) if total_total > 0 else 0

        return {
            "memory_used_gib": memory_info["memory_used_gib"],
            "memory_total_gib": memory_info["memory_total_gib"],
            "memory_percent": memory_info["memory_percent"],
            "swap_used_gib": memory_info["swap_used_gib"],
            "swap_total_gib": memory_info["swap_total_gib"],
            "swap_percent": memory_info["swap_percent"],
            "total_used": total_used,
            "total_total": total_total,
            "total_percent": total_percent,
        }


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Hosting(bot))
