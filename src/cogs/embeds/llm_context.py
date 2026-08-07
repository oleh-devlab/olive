from typing import ClassVar

import disnake
from disnake.ext import commands

from core.embed_cog import BaseEmbedCog
from modules.schedule_agent import schedule_context_manager


def _budget_field(budget) -> str:
    return (
        f"`Context:  {budget.context_tokens:,}`\n"
        f"`System:   {budget.reserved_system_tokens:,}`\n"
        f"`Memory:   {budget.reserved_memory_tokens:,}`\n"
        f"`Response: {budget.reserved_response_tokens:,}`\n"
        f"`Total:    {budget.total:,}`"
    )


def _add_context_fields(embed: disnake.Embed, manager, label: str) -> None:
    """
    Append one field per tracked context.
    Server and channel IDs are anonymized — only last three digits are shown.
    """
    if not manager.llm_context:
        return

    max_tokens = manager.token_budget.context_tokens
    for context_id, messages in manager.llm_context.items():
        total_tokens = sum(manager.get_message_tokens(m) for m in messages)
        msg_count = len(messages)

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
        embed.add_field(name=f"- {label} ...{str(context_id)[-3:]}", value=field_value, inline=False)


class LLMContextEmbed(BaseEmbedCog):
    embed_key = "llm_context"
    phrases_section = "llm_context_embed"
    settings_key = "llm_context_update_seconds"
    default_seconds = 30
    fallback_embed: ClassVar[dict] = {"title": ":brain: | LLM Context", "description": "Token usage per server context"}

    async def decorate(self, embed: disnake.Embed) -> None:
        olive_cog = self.bot.get_cog("AIAssistantCog")
        if olive_cog is None or not hasattr(olive_cog, "context_manager"):
            embed.description = "AI Assistant cog is not loaded."
            return

        # -- Token budgets (default + private side-by-side) --
        ctx_mgr = olive_cog.context_manager
        embed.add_field(name="Budget: default", value=_budget_field(ctx_mgr.token_budget), inline=True)
        embed.add_field(name="Budget: private", value=_budget_field(schedule_context_manager.token_budget), inline=True)

        _add_context_fields(embed, ctx_mgr, "ID")
        _add_context_fields(embed, schedule_context_manager, "Agent")

        if not ctx_mgr.llm_context and not schedule_context_manager.llm_context:
            embed.description = "No active contexts."


def setup(bot: commands.Bot) -> None:
    bot.add_cog(LLMContextEmbed(bot))
