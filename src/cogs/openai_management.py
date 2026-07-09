import disnake
from disnake.ext import commands
import logging

import core.cache as cache

logger = logging.getLogger(__name__)

class OpenAIManagerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.slash_command(name="openai", description="Manage OpenAI-compatible settings and context")
    @commands.is_owner()
    async def openai_group(self, inter: disnake.ApplicationCommandInteraction):
        pass

    # --- Config Management ---

    @openai_group.sub_command_group(name="config", description="Manage server configuration for OpenAI")
    async def config_group(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @config_group.sub_command(name="set_max_messages", description="Set the maximum number of messages in context")
    async def set_max_messages(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        max_messages: int = commands.Param(description="Number of messages to keep in history")
    ):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        manager.set_guild_max_messages(guild_id, max_messages)
        await manager.save_configs()
        
        await inter.response.send_message(f"Max messages for this server set to **{max_messages}**.", ephemeral=True)

    @config_group.sub_command(name="set_model", description="Override the default model for this server")
    async def set_model(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        model_name: str = commands.Param(default=None, description="Model name (leave empty to reset to default)")
    ):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        manager.set_guild_model(guild_id, model_name)
        await manager.save_configs()
        
        if model_name:
            await inter.response.send_message(f"Model for this server set to **{model_name}**.", ephemeral=True)
        else:
            await inter.response.send_message("Model override cleared. Using default model.", ephemeral=True)

    # --- Context Management ---

    @openai_group.sub_command_group(name="context", description="Manage message history context")
    async def context_group(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @context_group.sub_command(name="pop", description="Remove the last message and response from history")
    async def context_pop(self, inter: disnake.ApplicationCommandInteraction):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        if manager.pop_last_message(guild_id):
            await manager.write_to_file()
            await inter.response.send_message("Last message exchange has been removed from context.", ephemeral=True)
        else:
            await inter.response.send_message("Context is already empty.", ephemeral=True)

    @context_group.sub_command(name="clear", description="Clear all message history for this server")
    async def context_clear(self, inter: disnake.ApplicationCommandInteraction):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        manager.clear_context(guild_id)
        await manager.write_to_file()
        
        await inter.response.send_message("Message context cleared for this server.", ephemeral=True)

    @context_group.sub_command(name="archive", description="Archive the current context history")
    async def context_archive(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        name: str = commands.Param(description="Name of the archive to save")
    ):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        if manager.archive_context(guild_id, name):
            await manager.save_archives()
            await inter.response.send_message(f"Context successfully archived as **{name}**.", ephemeral=True)
        else:
            await inter.response.send_message("Context is empty. Nothing to archive.", ephemeral=True)

    @context_group.sub_command(name="restore", description="Restore history from an archive")
    async def context_restore(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        name: str = commands.Param(description="Name of the archive to restore")
    ):
        manager = getattr(cache, "openai_context_manager", None)
        if not manager:
            return await inter.response.send_message("OpenAI context manager is not loaded.", ephemeral=True)

        guild_id = str(inter.guild.id)
        if manager.restore_context(guild_id, name):
            await manager.write_to_file()
            await inter.response.send_message(f"Context successfully restored from **{name}**.", ephemeral=True)
        else:
            await inter.response.send_message(f"Archive **{name}** not found.", ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(OpenAIManagerCog(bot))
