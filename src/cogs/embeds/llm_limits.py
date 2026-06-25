import asyncio
import disnake
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data, get_phrases

class LLMLimitsEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_limits.start()

    def cog_unload(self):
        self.update_limits.cancel()
    
    @tasks.loop(seconds=30)
    async def update_limits(self):
        """
        Update the LLM limits embed with current consumption stats.
        """
        raw_embed_data = get_phrases().get("llm_limits_embed", {}).get("embed_data", {
            "title": "LLM API Limits",
            "description": "Current consumption of LLM models",
            "color": 0x2b2d31
        })
        
        formatted_embed_data = format_embed_data(raw_embed_data)
        embed = disnake.Embed.from_dict(formatted_embed_data)
        
        if getattr(core.cache, "llm_client", None):
            status_list = core.cache.llm_client.get_limits_status()
            for status in status_list:
                status_indicator = "🟢" if status["available"] else "🔴"
                field_name = f"{status_indicator} {status['model']}"
                field_value = (
                    f"**Req/Min:** {status['minute_req']}\n"
                    f"**Req/Day:** {status['day_req']}\n"
                    f"**Tok/Min:** {status['minute_tokens']}\n"
                    f"**Tok/Day:** {status['day_tokens']}"
                )
                embed.add_field(name=field_name, value=field_value, inline=False)
        else:
            embed.description = "LLM Client is not initialized or disabled."

        core.cache.embeds_to_send["llm_limits"] = embed

def setup(bot):
    bot.add_cog(LLMLimitsEmbed(bot))
