import asyncio
import disnake
from disnake.ext import commands
import logging
import time

from modules.llm_context_manager import LLMContextManager, UserMessageMetadata
from modules.llm_message_formatter import format_user_message
from modules.openai_context_manager import OpenAIContextManager
import core.cache as cache
from core.utils import get_phrases
import settings

logger = logging.getLogger(__name__)

# --- Optional AI Modules ---
try:
    from modules.llm_client import LLMClient  # noqa: E402
    from modules.llm_rate_limiter import RateLimitExceeded  # noqa: E402
    from modules.llm_response_gate import want_respond  # noqa: E402
    GEMINI_AVAILABLE = True
except ImportError as e:
    logger.warning("Gemini API modules failed to load: %s", e)
    GEMINI_AVAILABLE = False
    LLMClient = None
    RateLimitExceeded = Exception  # Dummy exception to avoid NameError
    want_respond = None

try:
    from modules.openai_client import OpenAIClient  # noqa: E402
    OPENAI_AVAILABLE = True
except ImportError as e:
    logger.warning("OpenAI API modules failed to load: %s", e)
    OPENAI_AVAILABLE = False
    OpenAIClient = None

try:
    from modules.schedule_agent import load_schedule_context, run_schedule_agent  # noqa: E402
    SCHEDULE_AGENT_AVAILABLE = True
except ImportError as e:
    logger.warning("Schedule Agent modules failed to load (possibly missing OR-Tools): %s", e)
    SCHEDULE_AGENT_AVAILABLE = False

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.context_manager = LLMContextManager()
        self.openai_context_manager = OpenAIContextManager()
        cache.openai_context_manager = self.openai_context_manager
        self.response_tasks = {}
        self.openai_client = None

        self.olive_enabled = True
        
        self.gemini_enabled = getattr(settings, "olive_enable_gemini", True) and GEMINI_AVAILABLE
        self.openai_enabled = getattr(settings, "olive_enable_openai", True) and OPENAI_AVAILABLE
        self.schedule_agent_enabled = getattr(settings, "olive_enable_schedule_agent", True) and SCHEDULE_AGENT_AVAILABLE

    async def cog_load(self):
        if self.gemini_enabled:
            try:
                cache.llm_client = LLMClient()
                logger.info(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))

                error = self.context_manager.token_budget.validate(cache.llm_client.min_context_tokens)
                if error:
                    logger.error(error + " LLM responses are disabled.")
                    cache.llm_client = None
            except Exception as e:
                logger.error("Error initializing LLMClient: %s", e)
                cache.llm_client = None
        else:
            cache.llm_client = None

        if self.openai_enabled:
            try:
                self.openai_client = OpenAIClient()
            except Exception as e:
                logger.error("Error initializing OpenAIClient: %s", e)
                self.openai_client = None
        else:
            self.openai_client = None

        await self.context_manager.load_from_file()
        await self.openai_context_manager.load_from_file()
        
        if self.schedule_agent_enabled:
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
            or not message.content
            or not isinstance(message.channel, disnake.TextChannel)
        ):
            return

        openai_test_channel = getattr(settings, "openai_test_channel_id", 0)
        is_openai_test = bool(openai_test_channel) and message.channel.id == openai_test_channel

        if is_openai_test:
            if not self.openai_enabled or not self.openai_client:
                return
        else:
            if not self.gemini_enabled or not cache.llm_client or not cache.llm_client.is_available:
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

        if self.schedule_agent_enabled and message.channel.id in cache.tasks_channels:
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

        anticipated_tokens = (len(system_instruction) // 2) + self.context_manager.get_total_tokens(guild_id)

        context = self.context_manager.get_interaction_context(guild_id)

        try:
            if not await want_respond(
                cache.llm_client, context, system_instruction, message.guild.id, anticipated_tokens=anticipated_tokens
            ):
                return

            async with message.channel.typing():
                response = await cache.llm_client.get_interaction(
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

    @commands.slash_command(name="turn_gemini", description="Enable or disable Gemini API locally")
    @commands.is_owner()
    async def turn_gemini(self, ctx: disnake.ApplicationCommandInteraction):
        if not GEMINI_AVAILABLE:
            await ctx.send("Gemini API modules are not available (missing dependencies).", ephemeral=True)
            return
            
        self.gemini_enabled = not self.gemini_enabled
        status = "enabled" if self.gemini_enabled else "disabled"
        await ctx.send(f"Gemini API is now {status}.", ephemeral=True)

    @commands.slash_command(name="turn_openai", description="Enable or disable OpenAI API locally")
    @commands.is_owner()
    async def turn_openai(self, ctx: disnake.ApplicationCommandInteraction):
        if not OPENAI_AVAILABLE:
            await ctx.send("OpenAI API modules are not available (missing dependencies).", ephemeral=True)
            return
            
        self.openai_enabled = not self.openai_enabled
        status = "enabled" if self.openai_enabled else "disabled"
        await ctx.send(f"OpenAI API is now {status}.", ephemeral=True)

    @commands.slash_command(name="turn_schedule", description="Enable or disable Schedule Agent locally")
    @commands.is_owner()
    async def turn_schedule(self, ctx: disnake.ApplicationCommandInteraction):
        if not SCHEDULE_AGENT_AVAILABLE:
            await ctx.send("Schedule Agent modules are not available (missing OR-Tools?).", ephemeral=True)
            return
            
        self.schedule_agent_enabled = not self.schedule_agent_enabled
        status = "enabled" if self.schedule_agent_enabled else "disabled"
        await ctx.send(f"Schedule Agent is now {status}.", ephemeral=True)

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
