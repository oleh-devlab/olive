import os

import disnake
from disnake.ext import commands

from settings import guilds

import json
import difflib

import core.cache
from core.utils import get_phrases

class PhrasesTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.slash_command(guild_ids=guilds)
    @commands.is_owner()
    async def reload_phrases(self, inter: disnake.ApplicationCommandInteraction):
        await core.utils.load_phrases()

        text = get_phrases(inter.guild.id).get("phrases_tools", {}).get("reload_phrases_response", "Error with getting message.")
        await inter.send(text, ephemeral=True)

    
    @commands.slash_command(
        name="edit_phrases",
        description="For current server",
        test_guilds=guilds
    )
    @commands.is_owner()
    async def edit_phrases(
        self, 
        inter: disnake.ApplicationCommandInteraction,
        key_path: str = commands.Param(description="Шлях до ключа (напр: utils/ping_response)"),
        action: str = commands.Param(description="Дія: читати, редагувати чи отримати файл", choices=["read", "edit", "download"], default="read"),
        value: str = commands.Param(description="Нове значення (для режиму редагування)", default=None)
    ):
        """
        TODO: global keys
        """
        await inter.response.defer(ephemeral=True)

        # Load JSON
        try:
            with open("phrases.json", "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

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

        elif action == "download":
            if os.path.exists("phrases.json"):
                await inter.edit_original_response(content="The file is attached.", file=disnake.File("phrases.json"))
            else:
                await inter.edit_original_response(content="File not found.")

def setup(bot):
    bot.add_cog(PhrasesTools(bot))
