import logging
import time

import disnake
import settings
from disnake.ext import commands

from core import cache
from core.token_manager import token_registry
from core.utils import TaskDebouncer, get_phrases, send_long_reply
from modules.llm_client import LLMClientPool
from modules.llm_context_manager import LLMContextManager
from modules.llm_message_formatter import FormattingProfile, format_user_message
from modules.llm_rate_limiter import RateLimitExceeded
from modules.llm_response_gate import want_respond
from modules.llm_token_budget import BudgetRepository
from modules.message_metadata import UserMessageMetadata
from modules.schedule_agent import (
    load_schedule_context,
    run_schedule_agent,
    schedule_context_manager,
)

logger = logging.getLogger(__name__)


class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.budget_name = "default"
        self.context_manager = LLMContextManager(
            token_budget=BudgetRepository.get_by_name(self.budget_name), budget_name=self.budget_name
        )

        self.response_debouncer = TaskDebouncer(self.bot.loop)
        self.schedule_debouncer = TaskDebouncer(self.bot.loop)

        self.olive_enabled = True

    async def cog_load(self):
        try:
            pool = LLMClientPool()

            default_token = token_registry.get_genai_token("default")
            if not default_token:
                raise ValueError("GenAI API token not found in tokens.json or GENAI_API_KEY env var")
            pool.register("default", default_token)

            # Register private role (for agent and private messages)
            private_token = token_registry.get_genai_token("private")
            if private_token:
                pool.register("private", private_token)

            cache.llm_pool = pool
            logger.info(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))

            error = self.context_manager.token_budget.validate(pool.default.min_context_tokens)
            if error:
                logger.error(error + " LLM responses are disabled.")
                cache.llm_pool = None
        except ValueError as e:
            logger.error("Error initializing LLMClient: %s", e)
            cache.llm_pool = None

        await self.context_manager.load_from_file()
        await load_schedule_context()

    def cog_unload(self):
        if cache.llm_pool:
            self.bot.loop.create_task(cache.llm_pool.shutdown_all())
            cache.llm_pool = None

            text = (
                get_phrases().get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            )
            logger.info(text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        bot_whitelist = getattr(settings, "olive_bot_whitelist", [])
        is_whitelisted_bot = message.author.bot and message.author.id in bot_whitelist

        if (
            not self.olive_enabled
            or (message.author.bot and not is_whitelisted_bot)
            or not cache.llm_pool
            or not message.content
            or not cache.llm_pool.is_available
            or not isinstance(message.channel, disnake.TextChannel)
        ):
            return

        guild_id = str(message.guild.id)
        has_consent = cache.llm_consent_manager.has_consent(message.author.id) if cache.llm_consent_manager else False

        meta = UserMessageMetadata.from_disnake_message(message)

        new_text = await format_user_message(message, meta, has_consent=has_consent)

        if not has_consent:
            # Deduplicate consecutive no-consent stubs from the same user
            if self.context_manager.is_duplicate_no_consent(guild_id, meta.author_name):
                return

            self.context_manager.add_user_message(
                guild_id,
                new_text,
                meta,
                no_consent=True,
            )
            return

        # Intercept schedule management in tasks_channel
        if message.channel.id in cache.tasks_channels:
            user_id = cache.tasks_channels[message.channel.id]

            # Format with AGENT profile (minimal: time + text only)
            agent_text = await format_user_message(
                message, meta, has_consent=has_consent, profile=FormattingProfile.AGENT
            )
            schedule_context_manager.add_user_message(str(message.channel.id), agent_text, meta)

            self.schedule_debouncer.submit(guild_id, 3, run_schedule_agent, self.bot, message, user_id)
            return

        self.context_manager.add_user_message(
            guild_id,
            new_text,
            meta,
        )

        self.response_debouncer.submit(guild_id, 5, self.generate_answer, message)

    @staticmethod
    def _resolve_system_instruction(guild_id) -> str:
        """
        Resolves the system instruction for a guild using a hierarchical approach:
        - Server-specific system_instruction takes priority over the global one.
        - system_instruction_addition is always appended (with two newlines) if present.
        """
        guild_olive = get_phrases(guild_id).get("olive", {})
        global_olive = get_phrases().get("olive", {})

        instruction = guild_olive.get("system_instruction") or global_olive.get(
            "system_instruction", "You're the AI assistant on the Discord server."
        )

        addition = guild_olive.get("system_instruction_addition")
        if addition:
            instruction = f"{instruction}\n\n{addition}"

        return instruction

    async def generate_answer(self, message: disnake.Message):
        guild_id = str(message.guild.id)
        system_instruction = self._resolve_system_instruction(message.guild.id)

        anticipated_tokens = (len(system_instruction) // 2) + self.context_manager.get_total_tokens(guild_id)

        context = self.context_manager.get_interaction_context(guild_id)

        try:
            llm_client = cache.llm_pool.default
            if not await want_respond(
                llm_client, context, system_instruction, message.guild.id, anticipated_tokens=anticipated_tokens
            ):
                return

            async with message.channel.typing():
                response = await llm_client.get_interaction(
                    context,
                    system_instruction=system_instruction,
                    max_output_tokens=self.context_manager.token_budget.reserved_response_tokens,
                    anticipated_tokens=anticipated_tokens,
                )

                candidate_tokens = 0
                if hasattr(response, "usage") and response.usage is not None:
                    prompt_tokens = getattr(response.usage, "total_input_tokens", 0)
                    candidate_tokens = getattr(response.usage, "total_output_tokens", 0)
                    if prompt_tokens > 0:
                        self.context_manager.update_latest_user_message_tokens(guild_id, prompt_tokens)

                out_text = getattr(response, "output_text", getattr(response, "text", ""))

                if not out_text:
                    logger.warning("Model returned empty response (possibly blocked by safety filters)")
                    return

                self.context_manager.add_interaction_steps(
                    guild_id,
                    response.steps,
                    tokens=candidate_tokens,
                    timestamp_ms=int(time.time() * 1000),
                )
                await send_long_reply(message, out_text, fail_if_not_exists=False, mention_author=False)

        except RateLimitExceeded:
            return
        except Exception as e:
            logger.error("Unexpected error in generate_answer: %s", e)
            return
        finally:
            self.context_manager.apply_restrictions()
            await self.context_manager.write_to_file()

    @commands.slash_command(name="turn_olive", description="Enable or disable OLIVE AI")
    @commands.is_owner()
    async def turn_olive(self, ctx: disnake.ApplicationCommandInteraction):
        self.olive_enabled = not self.olive_enabled
        status = "enabled" if self.olive_enabled else "disabled"
        text = (
            get_phrases(ctx.guild.id)
            .get("olive", {})
            .get("olive_status", "Olive is now {status}.")
            .format(status=status)
        )
        await ctx.send(text, ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))
