import logging

import disnake
from disnake.ext import commands

from core import cache
from core.llm_config import get_models, load_llm_config

logger = logging.getLogger(__name__)


class LLMConfigTools(commands.Cog):
    """Operator commands for `llm_config.json`, the phrases-free half of the LLM setup."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="reload_llm_config", description="Re-read llm_config.json")
    @commands.is_owner()
    async def reload_llm_config(self, ctx: disnake.ApplicationCommandInteraction):
        if not load_llm_config():
            text = "Could not read `llm_config.json` — the previous configuration is still in use."
            await ctx.send(text, ephemeral=True)
            return

        lines = [f"Reloaded `llm_config.json`: {len(get_models())} model(s) configured."]

        # Instructions are looked up per request, so they are already live. The
        # model list is not: an LLMClient builds its ModelConfig objects once, in
        # its constructor, and those objects carry the running rate-limit counters.
        if cache.llm_pool and cache.llm_pool.default:
            running = ", ".join(model.name for model in cache.llm_pool.default.models)
            lines.append(f"Instructions apply immediately. Models in use until a restart: `{running}`.")

        await ctx.send("\n".join(lines), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(LLMConfigTools(bot))
