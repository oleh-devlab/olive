import disnake
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data, get_phrases
import settings

UPDATE_SECONDS = getattr(settings, "llm_context_update_seconds", 30)


class LLMContextEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_context.start()

    def cog_unload(self):
        self.update_context.cancel()

    @tasks.loop(seconds=UPDATE_SECONDS)
    async def update_context(self):
        """
        Update the LLM context embed with per-guild token counts.
        Server IDs are anonymized — only last three digits are shown.
        """
        raw_embed_data = (
            get_phrases()
            .get("llm_context_embed", {})
            .get("embed_data", {"title": "LLM Context", "description": "Token usage per server context"})
        )

        formatted_embed_data = format_embed_data(raw_embed_data)
        embed = disnake.Embed.from_dict(formatted_embed_data)

        footer_text = (
            get_phrases()
            .get("utils", {})
            .get("update_interval", "Updates every {seconds} seconds.")
            .format(seconds=UPDATE_SECONDS)
        )
        embed.set_footer(text=footer_text)

        olive_cog = self.bot.get_cog("AIAssistantCog")
        if olive_cog is None or not hasattr(olive_cog, "context_manager"):
            embed.description = "AI Assistant cog is not loaded."
            core.cache.embeds_to_send["llm_context"] = embed
            return

        ctx_mgr = olive_cog.context_manager
        max_tokens = core.cache.llm_client.min_context_tokens if getattr(core.cache, "llm_client", None) else 128000

        if not ctx_mgr.llm_context:
            embed.description = "No active contexts."
            core.cache.embeds_to_send["llm_context"] = embed
            return

        for guild_id, messages in ctx_mgr.llm_context.items():
            total_tokens = sum(ctx_mgr.get_message_tokens(m) for m in messages)
            msg_count = len(messages)

            anonymous_id = f"ID ...{str(guild_id)[-3:]}"
            pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0

            if msg_count > 0 and max_tokens > 0:
                avg_tokens_per_msg = total_tokens / msg_count
                max_messages = int(max_tokens / avg_tokens_per_msg) if avg_tokens_per_msg > 0 else "unknown"
            else:
                max_messages = "unknown"

            field_name = f"- {anonymous_id}"
            field_value = (
                f"`Tokens: {total_tokens:,} / {max_tokens:,} ({pct:.1f}%)`\n"
                f"`Messages: {msg_count} / {max_messages}`"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        core.cache.embeds_to_send["llm_context"] = embed


def setup(bot):
    bot.add_cog(LLMContextEmbed(bot))
