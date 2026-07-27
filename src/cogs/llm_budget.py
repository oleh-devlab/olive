import disnake
from disnake.ext import commands
import logging

import core.cache as cache
from modules.llm_context_manager import LLMContextManager
from modules.llm_token_budget import BudgetRepository

logger = logging.getLogger(__name__)


class LLMBudgetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="token_budget", description="Manage LLM token budget")
    @commands.is_owner()
    async def token_budget(self, ctx: disnake.ApplicationCommandInteraction):
        pass

    @token_budget.sub_command(name="set", description="Update a token budget value")
    async def token_budget_set(
        self,
        ctx: disnake.ApplicationCommandInteraction,
        field: str = commands.Param(
            description="Budget field to update",
            choices=["context_tokens", "reserved_system_tokens", "reserved_memory_tokens", "reserved_response_tokens"],
        ),
        value: int = commands.Param(description="New value (tokens)", gt=0),
        name: str = commands.Param(
            description="Which token budget to update",
            choices=["default", "private"],
            default="default",
        ),
    ):
        ctx_mgr = LLMContextManager.get_by_budget(name)
        if ctx_mgr is None:
            await ctx.send(f"Unknown budget name: `{name}` (no context manager registered for it)", ephemeral=True)
            return

        budget = ctx_mgr.token_budget

        old_value = getattr(budget, field)
        setattr(budget, field, value)

        if cache.llm_pool and cache.llm_pool.default:
            error = budget.validate(cache.llm_pool.default.min_context_tokens)
            if error:
                setattr(budget, field, old_value)
                await ctx.send(f"Error: {error}", ephemeral=True)
                return

        try:
            BudgetRepository.save_to_db(name, budget)
        except Exception as e:
            logger.error("Failed to save token budget to DB: %s", e)
            setattr(budget, field, old_value)
            await ctx.send("Error: failed to save the new budget to the database. Changes reverted.", ephemeral=True)
            return

        ctx_mgr.apply_restrictions()
        await ctx_mgr.write_to_file()

        await ctx.send(
            f"**{name}** `{field}`: {old_value:,} → {value:,} (total: {budget.total:,})",
            ephemeral=True,
        )


def setup(bot: commands.Bot):
    bot.add_cog(LLMBudgetCog(bot))
