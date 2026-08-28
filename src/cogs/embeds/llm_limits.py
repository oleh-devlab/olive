from typing import ClassVar

import disnake
import settings
from disnake.ext import commands

import core.cache
from core.embed_cog import BaseEmbedCog
from core.utils import get_phrases
from modules.llm_limits_formatter import DEFAULT_CHAR_LIMIT, render_limits


class LLMLimitsEmbed(BaseEmbedCog):
    embed_key = "llm_limits"
    phrases_section = "llm_limits_embed"
    settings_key = "llm_limits_update_seconds"
    default_seconds = 30
    fallback_embed: ClassVar[dict] = {
        "title": ":robot: | LLM API Limits",
        "description": "Current consumption of LLM models",
    }

    async def decorate(self, embed: disnake.Embed) -> None:
        """Append current consumption stats, or say so when the pool never came up."""
        section = get_phrases().get(self.phrases_section, {})

        if not core.cache.llm_pool:
            embed.description = section.get("unavailable", "LLM Client is not initialized or disabled.")
            return

        intro = embed.description or ""
        table = render_limits(
            core.cache.llm_pool.get_unique_clients_status(),
            labels=section.get("labels"),
            collapse_idle=getattr(settings, "llm_limits_collapse_idle", True),
            char_limit=DEFAULT_CHAR_LIMIT - len(intro) - 1,
        )
        embed.description = f"{intro}\n{table}" if intro and table else (table or intro)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(LLMLimitsEmbed(bot))
