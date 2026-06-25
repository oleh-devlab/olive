import disnake
from disnake.ext import commands
import os
import asyncio
import re

import settings
import core.cache

import configparser
import logging

config = configparser.ConfigParser()


cogs_directory = settings.paths["cogs"]

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class ChatOps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def git_pull(self, inter: disnake.ApplicationCommandInteraction, remote: str = None, branch: str = None):
        await inter.response.defer(ephemeral=True)

        safe_pattern = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-\/]*$')

        if remote and not safe_pattern.match(remote):
            await inter.edit_original_response(content="Error in `remote`.")
            return

        if branch and not safe_pattern.match(branch):
            await inter.edit_original_response(content="Error in `branch`.")
            return
        
        content = ""
        if branch and not remote:
            remote = 'origin'
            content += f"No remote location was specified, so `{remote}` was selected.\n"

        cmd = ['git', 'pull']
        if remote:
            cmd.append(remote)
        if branch:
            cmd.append(branch)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=parent_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            content += f"\n```\n{stdout.decode('utf-8').strip()}\n```\nReload the cogs if needed."
            await inter.edit_original_response(
                content = content
            )
        else:
            content += f"Error occurred while running git pull:\n```\n{stderr.decode('utf-8').strip()}\n```"
            await inter.edit_original_response(
                content = content
            )
        
    def get_available_cogs(self) -> list:
        available_cogs = []
        for root, _, files in os.walk(cogs_directory):
            for file in files:
                if file.endswith('.py') and not file.startswith('__'):
                    rel_path = os.path.relpath(os.path.join(root, file), cogs_directory)
                    available_cogs.append(rel_path[:-3].replace(os.sep, '.'))
        return available_cogs

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def reload_cogs(self, inter: disnake.ApplicationCommandInteraction, cog_name: str = None):        
        if cog_name:
            extension_name = f"{cogs_directory}.{cog_name}"
            available_cogs = self.get_available_cogs()
            if cog_name in available_cogs:
                if extension_name in self.bot.extensions:
                    self.bot.reload_extension(extension_name)
                else:
                    self.bot.load_extension(extension_name)
                await inter.send(f"Cog '{cog_name}' has been restarted.", ephemeral=True)
            else:
                cog_list = "\n".join(available_cogs)
                await inter.send(f"No such file '{cog_name}'. Available cogs:\n```py\n{cog_list}\n```", ephemeral=True)
        else:
            extensions = list(self.bot.extensions.keys())
            for extension_name in extensions:
                if extension_name.startswith(f"{cogs_directory}."):
                    self.bot.reload_extension(extension_name)

            await inter.send("All loaded cogs have been restarted.", ephemeral=True)

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def unload_cogs(self, inter: disnake.ApplicationCommandInteraction, cog_name: str = None):
        if cog_name:
            extension_name = f"{cogs_directory}.{cog_name}"
            if extension_name in self.bot.extensions:
                self.bot.unload_extension(extension_name)
                await inter.send(f"Cog '{cog_name}' has been unloaded.", ephemeral=True)
            else:
                available_cogs = self.get_available_cogs()
                cog_list = "\n".join(available_cogs)
                await inter.send(f"Cog '{cog_name}' is not loaded. Available cogs:\n```py\n{cog_list}\n```", ephemeral=True)
        else:
            extensions = list(self.bot.extensions.keys())
            for extension_name in extensions:
                if extension_name.startswith(f"{cogs_directory}."):
                    self.bot.unload_extension(extension_name)
            await inter.send("All loaded cogs have been unloaded.", ephemeral=True)
    
    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def turn_debug_mode(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)

        async with core.cache.configLock:
            config.read(settings.paths["config_ini"])
            current_mode = config.getint('DEFAULT', 'debug_mode', fallback=0)

            new_mode =  int(not current_mode)

            config['DEFAULT']['debug_mode'] = f"{new_mode}"
            with open(settings.paths["config_ini"], 'w') as configfile:
                config.write(configfile)

            if new_mode == 1:
                logging.getLogger().setLevel(logging.DEBUG)
            else:
                logging.getLogger().setLevel(logging.WARNING)

        await inter.edit_original_response(f"Debug mode set to {new_mode}.")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def set_log_level(
        self, 
        inter: disnake.ApplicationCommandInteraction, 
        level: str = commands.Param(choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"])
    ):
        await inter.response.defer(ephemeral=True)
        numeric_level = getattr(logging, level)
        logging.getLogger().setLevel(numeric_level)
        await inter.edit_original_response(f"Global logging level set to **{level}**.")

def setup(bot):
    bot.add_cog(ChatOps(bot))
