import asyncio
import disnake
from disnake.ext import commands
from google.genai import types
import logging

from modules.llm_client import LLMClient
from modules.llm_rate_limiter import RateLimitExceeded
from modules.llm_context_manager import LLMContextManager
from modules.llm_consent_manager import LLMConsentManager
from modules.llm_message_formatter import format_user_message
from modules.llm_response_gate import want_respond
import core.cache as cache
from core.utils import get_phrases

logger = logging.getLogger(__name__)


class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.context_manager = LLMContextManager()
        self.response_tasks = {}

        self.olive_enabled = True

    async def cog_load(self):
        try:
            cache.llm_client = LLMClient()
            logger.info(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))
            
            await self.context_manager.load_from_file()
        except ValueError as e:
            logger.error("Error initializing LLMClient: %s", e)
            cache.llm_client = None

        cache.llm_consent = LLMConsentManager()
        await cache.llm_consent.load_from_file()

    def cog_unload(self):
        if cache.llm_client:
            self.bot.loop.create_task(cache.llm_client.shutdown())
            cache.llm_client = None

            text = get_phrases().get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            logger.info(text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        if (not self.olive_enabled 
            or message.author.bot 
            or not cache.llm_client 
            or not message.content
            or not message.guild
            or not cache.llm_client.is_available
        ):
            return
        
        guild_id = str(message.guild.id)
        has_consent = cache.llm_consent.has_consent(message.author.id) if cache.llm_consent else False

        new_text = await format_user_message(message, has_consent=has_consent)

        if not has_consent:
            # Deduplicate consecutive no-consent stubs from the same user
            if self.context_manager.is_duplicate_no_consent(guild_id, message.author.name):
                return
            
            self.context_manager.add_user_message(guild_id, new_text, no_consent=True)
            return

        self.context_manager.add_user_message(guild_id, new_text)

        if guild_id in self.response_tasks:
            self.response_tasks[guild_id].cancel()
            
        self.response_tasks[guild_id] = self.bot.loop.create_task(self.delayed_generate_answer(message))
        
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

        instruction = (
            guild_olive.get("system_instruction")
            or global_olive.get("system_instruction", "You're the AI assistant on the Discord server.")
        )

        addition = guild_olive.get("system_instruction_addition")
        if addition:
            instruction = f"{instruction}\n\n{addition}"

        return instruction

    async def generate_answer(self, message: disnake.Message):
        guild_id = str(message.guild.id)
        system_instruction = self._resolve_system_instruction(message.guild.id)
        context = self.context_manager.get_context(guild_id)

        try:
            if not await want_respond(cache.llm_client, context, system_instruction, message.guild.id):
                return

            reply_config = types.GenerateContentConfig(
                system_instruction=system_instruction, 
                max_output_tokens=1500
            )

            async with message.channel.typing():
                response = await cache.llm_client.get_response(context, reply_config)

                candidate_tokens = 0
                if hasattr(response, 'usage_metadata') and response.usage_metadata is not None:
                    prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                    candidate_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                    if prompt_tokens > 0:
                        self.context_manager.update_latest_user_message_tokens(guild_id, prompt_tokens)

                if not response.text:
                    logger.warning("Model returned empty response (possibly blocked by safety filters)")
                    return

                self.context_manager.add_model_message(guild_id, response.text, tokens=candidate_tokens)
                await message.reply(response.text, fail_if_not_exists=False, mention_author=False)

        except RateLimitExceeded:
            return
        except Exception as e:
            logger.error("Unexpected error in generate_answer: %s", e)
            return
        finally:
            limit = cache.llm_client.min_context_tokens if cache.llm_client else 128000
            self.context_manager.apply_restrictions(max_tokens=limit)
            await self.context_manager.write_to_file()

    @commands.slash_command(name="turn_olive", description="Enable or disable OLIVE AI")
    @commands.is_owner()
    async def turn_olive(self, ctx: disnake.ApplicationCommandInteraction):
        self.olive_enabled = not self.olive_enabled
        status = "enabled" if self.olive_enabled else "disabled"
        text = get_phrases(ctx.guild.id).get("olive", {}).get("olive_status", "Olive is now {status}.").format(status=status)
        await ctx.send(text, ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))
