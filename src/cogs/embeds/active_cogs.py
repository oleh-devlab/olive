import disnake
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data, get_phrases
from settings import paths
import settings
UPDATE_SECONDS = getattr(settings, 'active_cogs_update_seconds', 45)

cog_path = paths["cogs"]

class ActiveCogsEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.update_active_cogs.start()

    def cog_unload(self):
        self.update_active_cogs.cancel()

    @tasks.loop(seconds=UPDATE_SECONDS)
    async def update_active_cogs(self):
        formatted_cogs_list = "\n".join([f"[+] {cog_name.removeprefix(f'{cog_path}.')} - from {load_time}" for cog_name, load_time in core.cache.active_cogs_list.items()])
        
        raw_embed_data = get_phrases().get("active_cogs_embed", {}).get("embed_data", { "title": "Active Cogs", "description": "No data available." })
        formatted_embed_data = format_embed_data(raw_embed_data, formatted_cogs_list=formatted_cogs_list)

        embed = disnake.Embed.from_dict(formatted_embed_data)
        
        footer_text = get_phrases().get("utils", {}).get("update_interval", "Updates every {seconds} seconds.").format(seconds=UPDATE_SECONDS)
        embed.set_footer(text=footer_text)
        
        core.cache.embeds_to_send["active_cogs"] = embed

def setup(bot: commands.Bot) -> None:
    bot.add_cog(ActiveCogsEmbed(bot))
