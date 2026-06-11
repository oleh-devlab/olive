from email.mime import message

import disnake
from disnake.ext import commands
import core.cache
import json

from modules.google_genai import get_new_client, get_response

# This is a prototype cog for AI assistant functionality using Google GenAI.

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.llm_context = {} # {"channel_id": [...]]}

        self.max_messages_in_context = 25
        self.context_file_name = "llm_context.json"

        self.olive_enabled = False
        
        self.google_client = None

    async def cog_load(self):
        self.google_client = await get_new_client()
        print(core.cache.phrases.get("olive", {}).get("api_client_loaded", "API Google is loaded."))

        await self.load_context_from_file()

    def cog_unload(self):
        if self.google_client:
            self.bot.loop.create_task(self.google_client.aio.aclose())
            text = core.cache.phrases.get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            print(text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        if not self.olive_enabled or message.author.bot or not self.google_client or not message.content:
            return
        
        if str(message.channel.id) not in self.llm_context:
            self.llm_context[str(message.channel.id)] = []
        
        model_name = core.cache.phrases.get("olive", {}).get("model_name", "gemma-4-31b-it")

        self.llm_context[str(message.channel.id)].append({"role": "user", "parts": [{"text": f"[{message.author.display_name}][{message.author.name}]: \"{message.content}\""}]})
        async with message.channel.typing():
            response = await get_response(self.google_client, self.llm_context[str(message.channel.id)], model_name)

        self.llm_context[str(message.channel.id)].append({"role": "assistant", "parts": [{"text": response.text}]})

        await self.context_restrictions()
        await self.write_context_to_file()
        
        await message.reply(response.text, fail_if_not_exists=False, mention_author=False)

    async def context_restrictions(self):
        """
        For now, it's just a very simple restriction. It stops accepting new messages once the maximum limit is reached.
        """
        
        for channel_id, messages in self.llm_context.items():
            if len(messages) > self.max_messages_in_context:
                self.llm_context[channel_id] = messages[-self.max_messages_in_context:]

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
        text = core.cache.phrases.get("olive", {}).get("olive_status", "Olive is now {status}.").format(status=status)
        await ctx.send(text, ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))