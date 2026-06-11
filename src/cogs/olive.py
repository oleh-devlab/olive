import disnake
from disnake.ext import commands
import core.cache

from modules.google_genai import get_new_client, get_response

# This is a prototype cog for AI assistant functionality using Google GenAI.

class AIAssistantCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.channel_context = {} # "channel_id": [...]]

        self.olive_enabled = False
        
        self.google_client = None

    async def cog_load(self):
        self.google_client = await get_new_client()
        text = core.cache.phrases.get("olive", {}).get("api_client_loaded", "API Google is loaded.")
        print(text)

    def cog_unload(self):
        if self.google_client:
            self.bot.loop.create_task(self.google_client.aio.aclose())
            text = core.cache.phrases.get("olive", {}).get("api_client_closed", "Connection with Google GenAI is being closed.")
            print(text)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: disnake.Message):
        if not self.olive_enabled or message.author.bot or not self.google_client:
            return
        
        if str(message.channel.id) not in self.channel_context:
            self.channel_context[str(message.channel.id)] = []
        
        model_name = core.cache.phrases.get("olive", {}).get("model_name", "gemma-4-31b-it")

        self.channel_context[str(message.channel.id)].append({"role": "user", "parts": [{"text": f"[{message.author.display_name}][{message.author.name}]: \"{message.content}\""}]})
        response = await get_response(self.google_client, self.channel_context[str(message.channel.id)], model_name)
        self.channel_context[str(message.channel.id)].append({"role": "assistant", "parts": [{"text": response.text}]})
        
        await message.channel.send(response.text)

    @commands.slash_command(name="turn_olive", description="Enable or disable OLIVE AI")
    @commands.is_owner()
    async def turn_olive(self, ctx: disnake.ApplicationCommandInteraction):
        self.olive_enabled = not self.olive_enabled
        status = "enabled" if self.olive_enabled else "disabled"
        text = core.cache.phrases.get("olive", {}).get("olive_status", "Olive is now {status}.").format(status=status)
        await ctx.send(text, ephemeral=True)

def setup(bot: commands.Bot):
    bot.add_cog(AIAssistantCog(bot))