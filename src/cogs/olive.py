import asyncio
import disnake
from disnake.ext import commands
import json
from google.genai import types

from modules.llm_client import LLMClient
from modules.llm_rate_limiter import RateLimitExceeded
from modules.llm_context_manager import LLMContextManager
import core.cache as cache
from core.utils import get_phrases
from core.time_utils import tz

from datetime import datetime

import logging
logger = logging.getLogger(__name__)

days_uk = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.context_manager = LLMContextManager()
        self.response_tasks = {}

        self.olive_enabled = False

    async def cog_load(self):
        try:
            cache.llm_client = LLMClient()
            logger.info(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))
            
            await self.context_manager.load_from_file()
        except ValueError as e:
            logger.error("Error initializing LLMClient: %s", e)
            cache.llm_client = None

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

        dt_now = datetime.now(tz)
        day_name = days_uk[dt_now.weekday()]
        time_now = f"{day_name}, {dt_now.strftime('%d.%m.%Y %H:%M:%S')}"

        new_text = f"[{time_now}][{message.author.display_name}][{message.author.name}]: \"{message.content}\""
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

    async def generate_answer(self, message: disnake.Message):
        
        system_instruction = get_phrases(message.guild.id).get("olive", {}).get("system_instruction", "You're the AI assistant on the Discord server.")

        try:
            test_instruction_addition = get_phrases(message.guild.id).get("olive", {}).get("test_instruction_addition", None)
            if test_instruction_addition:
                test_system_instruction = f"{system_instruction}\n\n{test_instruction_addition}"

                test_schema = {
                    'properties': {
                        'i_should_answer': {
                            'description': 'True if the assistant should answer in the context, False otherwise.',
                            'type': 'boolean'
                        }
                    },
                    'required': ['i_should_answer'],
                    'type': 'object',
                }

                test_config = types.GenerateContentConfig(
                    system_instruction=test_system_instruction, 
                    response_mime_type="application/json",
                    response_json_schema=test_schema
                )
                test_response = await cache.llm_client.get_response(
                    self.context_manager.get_context(str(message.guild.id)), 
                    test_config,
                    cheap_first=True,
                )

                try:
                    if hasattr(test_response, 'parsed') and test_response.parsed is not None:
                        if isinstance(test_response.parsed, dict):
                            i_should_answer = test_response.parsed.get("i_should_answer", False)
                        else:
                            i_should_answer = getattr(test_response.parsed, "i_should_answer", False)
                    else:
                        raw_text = (test_response.text or "").strip()
                        
                        if raw_text.startswith("```"):
                            raw_text = raw_text[3:].strip()
                            if raw_text.lower().startswith("json"):
                                raw_text = raw_text[4:].strip()
                                
                        if raw_text.endswith("```"):
                            raw_text = raw_text[:-3].strip()
                        
                        data = json.loads(raw_text)
                        i_should_answer = data.get("i_should_answer", False)
                except Exception as e:
                    logger.error("Error parsing test response JSON: %s", e)
                    i_should_answer = False

                if not i_should_answer:
                    return

            reply_config = types.GenerateContentConfig(system_instruction=system_instruction, max_output_tokens=1500)

            async with message.channel.typing():
                response = await cache.llm_client.get_response(self.context_manager.get_context(str(message.guild.id)), reply_config)

                candidate_tokens = 0
                if hasattr(response, 'usage_metadata') and response.usage_metadata is not None:
                    prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0)
                    candidate_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0)
                    if prompt_tokens > 0:
                        self.context_manager.update_latest_user_message_tokens(str(message.guild.id), prompt_tokens)

                if not response.text:
                    logger.warning("Model returned empty response (possibly blocked by safety filters)")
                    return

                self.context_manager.add_model_message(str(message.guild.id), response.text, tokens=candidate_tokens)
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
