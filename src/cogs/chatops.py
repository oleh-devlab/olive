import disnake
from disnake.ext import commands
import os
import asyncio
import re

import configparser
config = configparser.ConfigParser()

import settings
import core.cache

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

        # --- git pull in console
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

        # --- Output result
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
        
    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def reload_cogs(self, inter: disnake.ApplicationCommandInteraction, cog_name: str = None):        
        if cog_name:
            cog_path = f"{cogs_directory}/{cog_name}.py"
            if os.path.exists(cog_path):
                extension_name = f"{cogs_directory}.{cog_name}"
                if extension_name in self.bot.extensions:
                    self.bot.unload_extension(extension_name)
                self.bot.load_extension(extension_name)

                await inter.send(f"Cog '{cog_name}' has been restarted.",ephemeral=True)
            else:
                cog_files = [f for f in os.listdir(cogs_directory) if f.endswith('.py')]
                cog_list = "\n".join(cog_files)
                await inter.send(f"No such file '{cog_name}'. Available cogs:\n```py\n{cog_list}\n```",ephemeral=True)
        else:
            cog_files = [f for f in os.listdir(cogs_directory) if f.endswith('.py')]
            for cog_file in cog_files:
                cog_name = cog_file[:-3]

                extension_name = f"{cogs_directory}.{cog_name}"
                if extension_name in self.bot.extensions:
                    self.bot.unload_extension(extension_name)
                self.bot.load_extension(extension_name)

            await inter.send("All cogs have been restarted.", ephemeral=True)

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def unload_cogs(self, inter: disnake.ApplicationCommandInteraction, cog_name: str = None):
        if cog_name:
            cog_path = f"{cogs_directory}/{cog_name}.py"
            if os.path.exists(cog_path):
                extension_name = f"{cogs_directory}.{cog_name}"
                if extension_name in self.bot.extensions:
                    self.bot.unload_extension(extension_name)
                    await inter.send(f"Cog '{cog_name}' has been unloaded.",ephemeral=True)
                else:
                    await inter.send(f"Cog '{cog_name}' is already unloaded.",ephemeral=True)
            else:
                cog_files = [f for f in os.listdir(cogs_directory) if f.endswith('.py')]
                cog_list = "\n".join(cog_files)
                await inter.send(f"No such file '{cog_name}'. Available cogs:\n```py\n{cog_list}\n```",ephemeral=True)
        else:
            cog_files = [f for f in os.listdir(cogs_directory) if f.endswith('.py')]
            for cog_file in cog_files:
                cog_name = cog_file[:-3]
                extension_name = f"{cogs_directory}.{cog_name}"
                if extension_name in self.bot.extensions:
                    self.bot.unload_extension(extension_name)
            await inter.send("All cogs have been unloaded.", ephemeral=True)
    
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

        await inter.edit_original_response(f"Debug mode set to {new_mode}.")

def setup(bot):
    bot.add_cog(ChatOps(bot))