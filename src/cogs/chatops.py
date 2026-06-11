import disnake
from disnake.ext import commands
import os
import asyncio
import re

import json
import difflib

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

    

    @commands.slash_command(
        name="edit_phrases",
        description="For current server",
        test_guilds=settings.guilds
    )
    @commands.is_owner()
    async def edit_phrases(
        self, 
        inter: disnake.ApplicationCommandInteraction,
        key_path: str = commands.Param(description="Шлях до ключа (напр: utils/ping_response)"),
        action: str = commands.Param(description="Дія: читати чи редагувати", choices=["read", "edit"], default="read"),
        value: str = commands.Param(description="Нове значення (для режиму редагування)", default=None)
    ):
        await inter.response.defer(ephemeral=True)

        # Load JSON
        try:
            with open("phrases.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        # Ізолюємо словник сервера
        guild_id = str(inter.guild.id)
        if guild_id not in data:
            data[guild_id] = {}

        current = data[guild_id]
        keys = key_path.split("/")

        for i, k in enumerate(keys[:-1]):
            if k not in current or not isinstance(current[k], dict):
                available = [f"`{key}`" for key in current.keys()] if isinstance(current, dict) else []
                await inter.edit_original_response(
                    content=f"Ключ `{k}` не знайдено або він не є словником на рівні `{'/'.join(keys[:i]) or 'root'}`.\nДоступні ключі: {', '.join(available) or 'Порожньо'}"
                )
                return
            current = current[k]

        last_key = keys[-1]

        
        if action == "read":
            if last_key not in current:
                available = [f"`{key}`" for key in current.keys()] if isinstance(current, dict) else []
                await inter.edit_original_response(
                    content=f"Ключ `{last_key}` не знайдено.\nДоступні ключі тут: {', '.join(available) or 'Порожньо'}"
                )
                return
            
            val = current[last_key]
            await inter.edit_original_response(
                content=f"Значення за ключем `{key_path}`:\n```json\n{json.dumps(val, ensure_ascii=False, indent=2)}\n```"
            )
            return

        
        elif action == "edit":
            if value is None:
                await inter.edit_original_response(content="Для редагування необхідно вказати параметр `value`.")
                return

            if last_key not in current:
                available = [f"`{key}`" for key in current.keys()] if isinstance(current, dict) else []
                await inter.edit_original_response(
                    content=f"Ключ `{last_key}` не знайдено для редагування.\nДоступні ключі: {', '.join(available) or 'Порожньо'}"
                )
                return

            old_val = current[last_key]

            try:
                new_val = json.loads(value)
            except json.JSONDecodeError:
                new_val = value

            current[last_key] = new_val

            with open("phrases.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            # Diff
            old_str = str(old_val)
            new_str = str(new_val)

            sm = difflib.SequenceMatcher(None, old_str, new_str)
            diff_out = []
            
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == 'equal':
                    diff_out.append(old_str[i1:i2])
                elif tag == 'delete':
                    diff_out.append(f"~~{old_str[i1:i2]}~~")
                elif tag == 'insert':
                    diff_out.append(f"**{new_str[j1:j2]}**")
                elif tag == 'replace':
                    diff_out.append(f"~~{old_str[i1:i2]}~~**{new_str[j1:j2]}**")

            diff_text = "".join(diff_out)

            await inter.edit_original_response(
                content=f"Updates `{key_path}`!\n\n**Diff:**\n{diff_text}"
            )
def setup(bot):
    bot.add_cog(ChatOps(bot))
