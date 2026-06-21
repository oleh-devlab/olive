import asyncio
import disnake
from disnake.ext import commands
import json
from google.genai import types
from modules.llm_client import LLMClient, RateLimitExceeded

from datetime import datetime

import core.cache as cache
from core.utils import get_phrases

from core.time_utils import tz

days_uk = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.llm_context = {} # {"guild_id": [...]]}
        self.response_tasks = {}

        self.max_messages_in_context = 26
        self.context_file_name = "llm_context.json"

        self.olive_enabled = False

    async def cog_load(self):
        try:
            cache.llm_client = LLMClient()
            print(get_phrases().get("olive", {}).get("api_client_loaded", "API Google is loaded."))
            
            await self.load_context_from_file()
        except ValueError as e:
            print(f"Error initializing LLMClient: {e}")
            cache.llm_client = None

    def cog_unload(self):
        if cache.llm_client:
            self.bot.loop.create_task(cache.llm_client.connection_close())
            cache.llm_client = None

            text = get_phrases().get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            print(text)

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
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []

        dt_now = datetime.now(tz)
        day_name = days_uk[dt_now.weekday()]
        time_now = f"{day_name}, {dt_now.strftime('%d.%m.%Y %H:%M:%S')}"

        new_text = f"[{time_now}][{message.author.display_name}][{message.author.name}]: \"{message.content}\""
        self.llm_context[guild_id].append({"role": "user", "parts": [{"text": new_text}]})

        if guild_id in self.response_tasks:
            self.response_tasks[guild_id].cancel()
            
        self.response_tasks[guild_id] = self.bot.loop.create_task(self.delayed_generate_answer(message))
        
    async def delayed_generate_answer(self, message: disnake.Message):
        try:
            await asyncio.sleep(3)
            await self.generate_answer(message)
        except asyncio.CancelledError:
            pass

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
                test_response = await cache.llm_client.get_response(self.llm_context[str(message.guild.id)], test_config)

                try:
                    if hasattr(test_response, 'parsed') and test_response.parsed is not None:
                        if isinstance(test_response.parsed, dict):
                            i_should_answer = test_response.parsed.get("i_should_answer", False)
                        else:
                            i_should_answer = getattr(test_response.parsed, "i_should_answer", False)
                    else:
                        data = json.loads(test_response.text)
                        i_should_answer = data.get("i_should_answer", False)
                except Exception as e:
                    print(f"Error parsing test response JSON: {e}")
                    i_should_answer = False

                if not i_should_answer:
                    await self.context_restrictions()
                    await self.write_context_to_file()
                    return

            reply_config = types.GenerateContentConfig(system_instruction=system_instruction, max_output_tokens=1500)

            async with message.channel.typing():
                response = await cache.llm_client.get_response(self.llm_context[str(message.guild.id)], reply_config)

        except RateLimitExceeded:
            return

        self.llm_context[str(message.guild.id)].append({"role": "model", "parts": [{"text": response.text}]})

        await self.context_restrictions()
        await self.write_context_to_file()
        
        await message.reply(response.text, fail_if_not_exists=False, mention_author=False)

    async def context_restrictions(self):
        """
        For now, it's just a very simple restriction. It stops accepting new messages once the maximum limit is reached.
        """
        
        for guild_id, messages in self.llm_context.items():
            if len(messages) > self.max_messages_in_context:
                sliced_messages = messages[-self.max_messages_in_context:]
            
                # Deleting first model message if it is at beginning of the context
                # --- I'm not sure yet if the API actually prohibits this, so I'm just playing it safe.
                while sliced_messages and sliced_messages[0].get("role") in ["assistant", "model"]:
                    sliced_messages.pop(0)
                    
                self.llm_context[guild_id] = sliced_messages

    async def load_context_from_file(self):
        try:
            with open(self.context_file_name, "r", encoding="utf-8") as f:
                self.llm_context = json.load(f)
            print("LLM context is loaded from file.")

        except FileNotFoundError:
            print("Context file not found. Starting with an empty context.")
            self.llm_context = {}
        except json.JSONDecodeError:
            print("Context file is invalid. Starting with an empty context.")
            self.llm_context = {}
        except Exception as e:
            print(f"Error loading LLM context from file: {e}")
            self.llm_context = {}

    async def write_context_to_file(self):
        with open(self.context_file_name, "w", encoding="utf-8") as f:
            json.dump(self.llm_context, f, ensure_ascii=False, indent=4)

    @commands.slash_command(name="turn_olive", description="Enable or disable OLIVE AI")
    @commands.is_owner()
    async def turn_olive(self, ctx: disnake.ApplicationCommandInteraction):
        self.olive_enabled = not self.olive_enabled
        status = "enabled" if self.olive_enabled else "disabled"
        text = get_phrases(ctx.guild.id).get("olive", {}).get("olive_status", "Olive is now {status}.").format(status=status)
        await ctx.send(text, ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))
