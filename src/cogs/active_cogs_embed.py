import disnake
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data

class ActiveCogsEmbed(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.update_active_cogs.start()

    def cog_unload(self):
        self.update_active_cogs.cancel()

    @tasks.loop(seconds=45)
    async def update_active_cogs(self):
        formatted_cogs_list = "\n".join([f"[+] {cog_name} - from {load_time}" for cog_name, load_time in core.cache.active_cogs_list.items()])
        
        raw_embed_data = core.cache.phrases.get("active_cogs_embed", {}).get("embed_data", { "title": "Active Cogs", "description": "No data available." })
        formatted_embed_data = format_embed_data(raw_embed_data, formatted_cogs_list=formatted_cogs_list)

        embed = disnake.Embed.from_dict(formatted_embed_data)
        core.cache.embeds_to_send["active_cogs"] = embed

def setup(bot: commands.Bot) -> None:
    bot.add_cog(ActiveCogsEmbed(bot))
