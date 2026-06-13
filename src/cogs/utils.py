import disnake
from disnake.ext import commands
import os
import asyncio
from datetime import datetime

from zoneinfo import ZoneInfo

from settings import paths, channels, main_guild_id, guilds
import core.cache
from core.utils import get_phrases

import configparser
config = configparser.ConfigParser()


config_dir_setting = paths["config_ini"]
guild_id = main_guild_id
terminal_id = channels["terminal_channel"]

parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_file_path = os.path.join(parent_dir, config_dir_setting)

tz = ZoneInfo('Europe/Kyiv')

class Utils(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_connect(self):
        time_now = datetime.now(ZoneInfo("Europe/Kyiv")).strftime('%d.%m.%Y %H:%M:%S')

        text = get_phrases().get("utils", {}).get("on_connected", "Bot connected at {time_now}.").format(time_now=time_now)
        print(text)
    
    @commands.Cog.listener()
    async def on_resumed(self):
        time_now = datetime.now(ZoneInfo("Europe/Kyiv")).strftime('%d.%m.%Y %H:%M:%S')

        text = get_phrases().get("utils", {}).get("on_resumed", "Bot resumed at {time_now}.").format(time_now=time_now)
        print(text)
    
    
    @commands.Cog.listener()
    async def on_disconnect(self):
        time_now = datetime.now(ZoneInfo("Europe/Kyiv")).strftime('%d.%m.%Y %H:%M:%S')

        text = get_phrases().get("utils", {}).get("on_disconnect", "Bot disconnected at {time_now}.").format(time_now=time_now)
        print(text)

    @commands.slash_command(guild_ids=guilds)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ping(self, inter: disnake.ApplicationCommandInteraction):
        latency = f"{self.bot.latency * 1000:.1f}"

        text = get_phrases(inter.guild.id).get("utils", {}).get("ping_response", "Error with getting message. Ping: {latency} ms.").format(latency=latency)
        await inter.send(text)

    async def check_stats(self):
        await asyncio.sleep(2)
        async with core.cache.configLock:
            config.read(config_file_path)
            online_members = sum(1 for member in self.bot.get_guild(guild_id).members if member.status != disnake.Status.offline)
            config_online = config.getint('DEFAULT', 'max_online', fallback=0)
            if online_members > config_online:
                text = get_phrases(guild_id).get("utils", {}).get("max_online_record", "New online users record: **{online_members}**").format(online_members=online_members)
                await self.bot.get_or_fetch_channel(terminal_id).send(text)
                config.set('DEFAULT', 'max_online', str(online_members))
            
            with open(config_file_path, 'w') as configfile:
                config.write(configfile)
    
    @commands.Cog.listener()
    async def on_message(self, message):
        return

        if message.author.bot:
            pass
        else:
            # async with core.cache.configLock:
            #     config.read(config_file_path)
            #     config.set('DEFAULT', 'messanges_of_week', (config.getint('DEFAULT', 'messanges_of_week')+1))
            #     with open(config_file_path, 'w') as configfile:
            #         config.write(configfile)
            pass

        await self.check_stats()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        return
        await self.check_stats()



def setup(bot):
    bot.add_cog(Utils(bot))
