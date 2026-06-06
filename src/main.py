import disnake
from disnake.ext import commands
from disnake import Activity, ActivityType
import os
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json

import core.bot

import core.cache, core.utils

import configparser
config = configparser.ConfigParser()

intents = disnake.Intents().all()
intents.messages = True
intents.members = True
intents.voice_states = True
intents.guilds = True


from settings import *
cogs_directory = paths["cogs"]
token_file_path = paths["token_file"]
config_ini_path = paths["config_ini"]

test_guilds_list = guilds

bot = core.bot.OliveBot(command_prefix='!',intents=intents,test_guilds=guilds)
bot.remove_command('help')

channel_for_bot_news = channels["bot_news"]

tz = ZoneInfo('Europe/Kyiv')

@bot.event
async def on_ready():
    if core.cache.configLock is None:
        core.cache.configLock = asyncio.Lock()

    Note = ""
    utc_time = datetime.now(timezone.utc)
    local_time = utc_time.astimezone(tz)
    formatted_time = local_time.strftime('%d.%m.%Y %H:%M:%S')
    print(f'[INFO] : [{formatted_time}] : on_ready called')

    async with core.cache.configLock:
        config.read(config_ini_path)

        debug_mode = config.getint('DEFAULT', 'debug_mode', fallback=None)
        print(f'[CONFIG in start bot] debug_mode: {debug_mode}')
        if debug_mode is None:
            debug_mode = 0
            Note += "[Warning]: debug_mode not found, temporary value: `0`\n"

        current_time = datetime.now(timezone.utc)

        last_time_in_cfg = config.get('DEFAULT', 'last_run_time', fallback=None)
        if last_time_in_cfg is not None:
            last_time = datetime.strptime(last_time_in_cfg, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

            print(f'[CONFIG in start bot] last_run_time: {last_time}')
            time_difference = current_time - last_time
            print(f'[CONFIG in start bot] time_difference of last_run_time: ...\n...{time_difference}')
        else:
            time_difference = None
            Note += "[Warning]: last_run_time not found, but a new value will be written.\n"

        config['DEFAULT']['last_run_time'] = current_time.strftime("%Y-%m-%d %H:%M:%S")
        with open(config_ini_path, 'w') as configfile:
            config.write(configfile)
        print('[CONFIG] New last_run_time written')

    if not debug_mode:
        if time_difference is not None:
            if time_difference.total_seconds() <= safe_seconds_before_start:
                Note += "[Warning]: not enough time has passed since the last run. asyncio.sleep({safe_seconds_before_start}) started before the end of the run."
                print(f'')
                await asyncio.sleep(safe_seconds_before_start)
        else:
            print('[INFO] Error of last_run_time.\nRunning asyncio.sleep(15)...')
            await asyncio.sleep(15)

        time_difference = str(time_difference).split('.', 1)[0]
        
        Note = Note if Note else "None."

        final_message = core.cache.phrases.get("main", {}).get("on_ready", "Bot started at {formatted_time}. Notes: {Note}. Error with taking phrases.").format(formatted_time=formatted_time, time_difference=time_difference, Note=Note)

        channel = bot.get_channel(channel_for_bot_news)
        await channel.send(final_message)
        print(f'\n[INFO of Discord] : {final_message}\n')

    await bot.change_presence(activity=Activity(type=ActivityType.watching,name="Так",state='Існує.'))
    print('[INFO] bot.change_presence is done')

for file in os.listdir(f'./{cogs_directory}'):
    if file.endswith(".py") and file != 'info.py':
        print(f'[INFO] {file} loading from main...')
        bot.load_extension(f"{cogs_directory}.{file[:-3]}")

if __name__ == '__main__':
    print('[INFO] bot.run() trying to start...')

    asyncio.run(core.utils.load_phrases())

    if not os.path.exists(token_file_path):
        text = core.cache.phrases.get("main", {}).get("token_file_not_found", "[Error] Token file not found at {token_file_path}. Bot cannot start.").format(token_file_path=token_file_path)
        print(text)
    else:
        with open(token_file_path, 'r') as f:
            token = f.read().strip()
        
        if not (token is None):
            bot.run(token)
