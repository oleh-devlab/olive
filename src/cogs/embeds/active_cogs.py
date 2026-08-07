from disnake.ext import commands
from settings import paths

import core.cache
from core.embed_cog import BaseEmbedCog

cog_path = paths["cogs"]


class ActiveCogsEmbed(BaseEmbedCog):
    embed_key = "active_cogs"
    phrases_section = "active_cogs_embed"
    settings_key = "active_cogs_update_seconds"
    default_seconds = 45
    fallback_embed = {"title": ":electric_plug: | Active Cogs", "description": "No data available."}

    async def get_data(self):
        formatted_cogs_list = "\n".join(
            [
                f"[+] {cog_name.removeprefix(f'{cog_path}.')} - from {load_time}"
                for cog_name, load_time in core.cache.active_cogs_list.items()
            ]
        )

        return {"formatted_cogs_list": formatted_cogs_list}


def setup(bot: commands.Bot) -> None:
    bot.add_cog(ActiveCogsEmbed(bot))
