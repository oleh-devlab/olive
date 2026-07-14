import asyncio
import disnake
from disnake.ext import commands
from google.genai import types
import logging
import time

from modules.llm_client import LLMClient
from modules.llm_rate_limiter import RateLimitExceeded
from modules.llm_context_manager import LLMContextManager, UserMessageMetadata
from modules.llm_message_formatter import format_user_message
from modules.llm_response_gate import want_respond
from modules.schedule_agent import load_schedule_context, run_schedule_agent
import core.cache as cache
from core.utils import get_phrases
from modules.openai_client import OpenAIClient
from modules.openai_context_manager import OpenAIContextManager
import settings

logger = logging.getLogger(__name__)


class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.context_manager = LLMContextManager()
        self.openai_context_manager = OpenAIContextManager()
        cache.openai_context_manager = self.openai_context_manager
        self.response_tasks = {}
        self.openai_client = None

        self.olive_enabled = True

    async def cog_load(self):
        try:
            cache.llm_client = LLMClient()
            logger.info(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))

            error = self.context_manager.token_budget.validate(cache.llm_client.min_context_tokens)
            if error:
                logger.error(error + " LLM responses are disabled.")
                cache.llm_client = None
        except ValueError as e:
            logger.error("Error initializing LLMClient: %s", e)
            cache.llm_client = None

        self.openai_client = OpenAIClient()

        await self.context_manager.load_from_file()
        await self.openai_context_manager.load_from_file()
        await load_schedule_context()

    def cog_unload(self):
        if cache.llm_client:
            self.bot.loop.create_task(cache.llm_client.shutdown())
            cache.llm_client = None

            text = (
                get_phrases().get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            )
            logger.info(text)
            
        if self.openai_client:
            self.bot.loop.create_task(self.openai_client.shutdown())
            self.openai_client = None

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        if (
            not self.olive_enabled
            or message.author.bot
            or not cache.llm_client
            or not message.content
            or not cache.llm_client.is_available
        ):
            return

        openai_test_channel = getattr(settings, "openai_test_channel_id", 0)
        is_openai_test = message.channel.id == openai_test_channel

        if is_openai_test:
            if not self.openai_client:
                return
        else:
            if not cache.llm_client or not cache.llm_client.is_available:
                return

        guild_id = str(message.guild.id)
        has_consent = cache.llm_consent.has_consent(message.author.id) if getattr(cache, "llm_consent", None) else False

        meta = UserMessageMetadata.from_message(message)

        new_text = await format_user_message(message, meta, has_consent=has_consent)

        if is_openai_test:
            if not has_consent:
                if self.openai_context_manager.is_duplicate_no_consent(guild_id, message.author.name):
                    return
                self.openai_context_manager.add_user_message(guild_id, new_text, no_consent=True)
                return

            self.openai_context_manager.add_user_message(guild_id, new_text)
            self.bot.loop.create_task(self.generate_openai_answer(message))
            return

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
        if not hasattr(cache, "tasks_channels"):
            cache.tasks_channels = {}

        if message.channel.id in cache.tasks_channels:
            user_id = cache.tasks_channels[message.channel.id]
            self.bot.loop.create_task(run_schedule_agent(self.bot, message, user_id, new_text, meta))
            return

        self.context_manager.add_user_message(
            guild_id,
            new_text,
            meta,
        )

        if guild_id in self.response_tasks:
            self.response_tasks[guild_id].cancel()

        self.response_tasks[guild_id] = self.bot.loop.create_task(self.delayed_generate_answer(message))

    async def generate_openai_answer(self, message: disnake.Message):
        guild_id = str(message.guild.id)
        system_instruction = self._resolve_system_instruction(message.guild.id)
        context = self.openai_context_manager.get_context(guild_id)
        
        guild_config = self.openai_context_manager.get_guild_config(guild_id)
        model_override = guild_config.get("model_name")

        try:
            async with message.channel.typing():
                response_text = await self.openai_client.get_response(context, system_instruction, model_override=model_override)
                
                if not response_text:
                    logger.warning("OpenAI Model returned empty response")
                    return

                self.openai_context_manager.add_model_message(guild_id, response_text)
                await message.reply(response_text, fail_if_not_exists=False, mention_author=False)

        except Exception as e:
            logger.error("Unexpected error in generate_openai_answer: %s", e)
            return
        finally:
            self.openai_context_manager.apply_restrictions(guild_id=guild_id)
            await self.openai_context_manager.write_to_file()

    async def delayed_generate_answer(self, message: disnake.Message):
        try:
            await asyncio.sleep(3)
            await self.generate_answer(message)
        except asyncio.CancelledError:
            pass
        finally:
            guild_id = str(message.guild.id)
            if self.response_tasks.get(guild_id) == asyncio.current_task():
                del self.response_tasks[guild_id]

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
        context = self.context_manager.get_interaction_context(guild_id)

        try:
            if not await want_respond(cache.llm_client, context, system_instruction, message.guild.id):
                return

            async with message.channel.typing():
                response = await cache.llm_client.get_interaction(
                    context, 
                    system_instruction=system_instruction, 
                    max_output_tokens=1500
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
                await message.reply(out_text, fail_if_not_exists=False, mention_author=False)

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
    ):
        budget = self.context_manager.token_budget

        old_value = getattr(budget, field)
        setattr(budget, field, value)

        if cache.llm_client:
            error = budget.validate(cache.llm_client.min_context_tokens)
            if error:
                setattr(budget, field, old_value)
                await ctx.send(f"Error: {error}", ephemeral=True)
                return

        budget.save_to_file()

        self.context_manager.apply_restrictions()
        await self.context_manager.write_to_file()

        await ctx.send(
            f"`{field}`: {old_value:,} → {value:,} (total: {budget.total:,})",
            ephemeral=True,
        )


def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))
