import disnake
from disnake.ext import commands
import json
from google.genai import types
from modules.google_genai import LLMClient

from datetime import datetime
from zoneinfo import ZoneInfo

import core.cache as cache

# This is a prototype cog for AI assistant functionality using Google GenAI.

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.llm_context = {} # {"guild_id": [...]]}

        self.max_messages_in_context = 26
        self.context_file_name = "llm_context.json"

        self.olive_enabled = False

    async def cog_load(self):
        cache.llm_client = LLMClient()
        print(cache.phrases.get("olive", {}).get("api_client_loaded", "API Google is loaded."))

        await self.load_context_from_file()

    def cog_unload(self):
        if cache.llm_client:
            self.bot.loop.create_task(cache.llm_client.connection_close())
            text = cache.phrases.get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            print(text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        if not self.olive_enabled or message.author.bot or not cache.llm_client or not message.content:
            return
        
        if str(message.guild.id) not in self.llm_context:
            self.llm_context[str(message.guild.id)] = []

        time_now = datetime.now(ZoneInfo("Europe/Kyiv")).strftime('%d.%m.%Y %H:%M:%S')

        self.llm_context[str(message.guild.id)].append({"role": "user", "parts": [{"text": f"[{time_now}][{message.author.display_name}][{message.author.name}]: \"{message.content}\""}]})
        
        system_instruction = cache.phrases.get("olive", {}).get("system_instruction", "You're the AI assistant on the Discord server.")

        test_instruction_addition = cache.phrases.get("olive", {}).get("test_instruction_addition", None)
        if test_instruction_addition:
            test_system_instruction = f"{system_instruction}\n\n{test_instruction_addition}"

            test_config = types.GenerateContentConfig(system_instruction=test_system_instruction, response_mime_type="application/json")
            test_response = await cache.llm_client.get_response(self.llm_context[str(message.guild.id)], test_config)

            try:
                data = json.loads(test_response.text)
                i_should_answer = data["i_should_answer"]
            except Exception as e:
                print(f"Error parsing test response JSON: {e}")
                i_should_answer = False

            if not i_should_answer:
                await self.context_restrictions()
                await self.write_context_to_file()
                return

        reply_config = types.GenerateContentConfig(system_instruction=system_instruction, max_output_tokens=1500)

        model_name = cache.phrases.get("olive", {}).get("model_name", "gemma-4-31b-it")
        cache.llm_client.model_name = model_name

        async with message.channel.typing():
            response = await cache.llm_client.get_response(self.llm_context[str(message.guild.id)], reply_config)

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
        text = cache.phrases.get("olive", {}).get("olive_status", "Olive is now {status}.").format(status=status)
        await ctx.send(text, ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))