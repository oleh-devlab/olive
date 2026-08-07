import disnake
import settings
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data, get_phrases
from modules.schedule_agent import schedule_context_manager

UPDATE_SECONDS = getattr(settings, "llm_context_update_seconds", 30)


def _budget_field(budget) -> str:
    return (
        f"`Context:  {budget.context_tokens:,}`\n"
        f"`System:   {budget.reserved_system_tokens:,}`\n"
        f"`Memory:   {budget.reserved_memory_tokens:,}`\n"
        f"`Response: {budget.reserved_response_tokens:,}`\n"
        f"`Total:    {budget.total:,}`"
    )


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
            .get("embed_data", {"title": ":brain: | LLM Context", "description": "Token usage per server context"})
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

        # -- Token budgets (default + private side-by-side) --
        ctx_mgr = olive_cog.context_manager
        embed.add_field(name="Budget: default", value=_budget_field(ctx_mgr.token_budget), inline=True)
        embed.add_field(name="Budget: private", value=_budget_field(schedule_context_manager.token_budget), inline=True)

        # -- Context usage: main (olive) --
        if ctx_mgr.llm_context:
            max_tokens = ctx_mgr.token_budget.context_tokens
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

                field_value = (
                    f"`Tokens: {total_tokens:,} / {max_tokens:,} ({pct:.1f}%)`\n"
                    f"`Messages: {msg_count} / {max_messages}`"
                )
                embed.add_field(name=f"- {anonymous_id}", value=field_value, inline=False)

        # -- Context usage: schedule agent (private) --
        if schedule_context_manager.llm_context:
            max_tokens = schedule_context_manager.token_budget.context_tokens
            for channel_id, messages in schedule_context_manager.llm_context.items():
                total_tokens = sum(schedule_context_manager.get_message_tokens(m) for m in messages)
                msg_count = len(messages)

                anonymous_id = f"Agent ...{str(channel_id)[-3:]}"
                pct = (total_tokens / max_tokens * 100) if max_tokens > 0 else 0

                if msg_count > 0 and max_tokens > 0:
                    avg_tokens_per_msg = total_tokens / msg_count
                    max_messages = int(max_tokens / avg_tokens_per_msg) if avg_tokens_per_msg > 0 else "unknown"
                else:
                    max_messages = "unknown"

                field_value = (
                    f"`Tokens: {total_tokens:,} / {max_tokens:,} ({pct:.1f}%)`\n"
                    f"`Messages: {msg_count} / {max_messages}`"
                )
                embed.add_field(name=f"- {anonymous_id}", value=field_value, inline=False)

        if not ctx_mgr.llm_context and not schedule_context_manager.llm_context:
            embed.description = "No active contexts."

        core.cache.embeds_to_send["llm_context"] = embed


def setup(bot):
    bot.add_cog(LLMContextEmbed(bot))
