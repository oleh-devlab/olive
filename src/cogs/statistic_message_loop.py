import asyncio
import aiohttp
from datetime import datetime
import time
from disnake.ext import commands, tasks
import traceback
import disnake

from settings import channels, owner_id, embeds_blacklist

import core.cache
from core.utils import get_phrases

from core.time_utils import tz

class MessageLoop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channels = []
        self.messages = []

        self.last_embeds_dicts = {}
        
        self.channels_valid_embeds = {}

        self.retries_5xx = 0

        self.base_delay = 5
        self.max_delay = 150

        self.last_error_time = 0.0

        self.main_loop.start()


    def cog_unload(self):
        self.main_loop.cancel()

    @tasks.loop(seconds=10)
    async def main_loop(self):
        now = datetime.now(tz)
        formatted_time = now.strftime('%d.%m.%Y %H:%M:%S')
        content = f"`{formatted_time} UTC+2`"

        # TODO: Optimize this

        for channel in self.channels:
            valid_embeds = []
            channel_id = channel.id
            channel_guild_id = channel.guild.id

            for emb_name, emb in core.cache.embeds_to_send.items():
                if (emb is not None) and (emb_name not in embeds_blacklist.get(channel_guild_id, [])):
                    valid_embeds.append(emb)
            
            self.channels_valid_embeds[channel_id] = valid_embeds
        
        # self.channels_valid_embeds = {channel_id: [emb1, emb2, ...]}

        # Compare current embeds to avoid unnecessary edits
        new_embeds_dicts = {}
        for channel_id, embeds in self.channels_valid_embeds.items():
            try:
                new_embeds_dicts[channel_id] = [e.to_dict() for e in embeds]
            except Exception:
                print(f"Error converting new embeds to dicts for {content} in channel {channel_id}.")
                new_embeds_dicts[channel_id] = []            

        for message in self.messages:
            message_channel_id = message.channel.id
            if self.last_embeds_dicts.get(message_channel_id, []) == new_embeds_dicts.get(message_channel_id, []):
                # print(f"Embeds are the same, skipping edit for {content} in channel {message_channel_id}.")
                continue

            await message.edit(content=content, embeds=self.channels_valid_embeds[message_channel_id])
            await asyncio.sleep(0.5)

        self.last_embeds_dicts = new_embeds_dicts

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()
        
        try:
            self.channels = []
            self.channels_valid_embeds = {}
            self.messages = []
            
            # 1. Getting channels
            for channel_id in channels["statistic"]:
                try:
                    channel = await self.bot.get_or_fetch_channel(channel_id)
                    self.channels.append(channel)
                    self.channels_valid_embeds[channel.id] = []
                except Exception as e:
                    print(f"[before_main_loop WARNING] Not found channel {channel_id}: {e}")

            # 2. Cleaning the detected channels
            for channel in self.channels:
                await asyncio.sleep(0.5)
                try:
                    await channel.purge()
                except Exception as e:
                    print(f"[ERROR before_main_loop : purge] Error purging channel {channel.id}: {e}")

            # 3. Sending initial messages and filling the list for future edits
            await asyncio.sleep(0.5)
            
            for channel in self.channels:
                try:
                    text = get_phrases(channel.guild.id).get("statistic_message_loop", {}).get("welcome_message", "Error with getting message for statistic channel.")
                    msg = await channel.send(text)
                    self.messages.append(msg)
                    print(f"Initial message sent to channel {channel.id} for MessageLoop.")
                except Exception as e:
                    print(f"[ERROR before_main_loop : send initial message] Error sending initial message to channel {channel.id}: {e}")

        except Exception as e:
            print(f"[ERROR in before_main_loop]: {e}")
            traceback.print_exc()

    @main_loop.error
    async def on_main_loop_error(self, error):
        traceback.print_exc()
        is_server_error = isinstance(error, disnake.errors.HTTPException) and error.status >= 500
        is_connection_error = isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError))

        if is_server_error or is_connection_error:
            current_time = time.time()

            if current_time - self.last_error_time > 600:
                self.retries_5xx = 0
                
            self.last_error_time = current_time

            err_type = f"HTTP {error.status}" if hasattr(error, 'status') else "Network error"
            time_now = datetime.now(tz).strftime('%d.%m.%Y %H:%M:%S')
            print(f"[{time_now}] {err_type} from Discord API.")

            delay = min(self.max_delay, self.base_delay * (2 ** self.retries_5xx))
            print(f"Attempt: {self.retries_5xx}. Delay before restart: {delay} seconds.")

            # Notify the channel only when we first reach the maximum delay or this is the first time
            if (delay == self.base_delay) or (delay == self.max_delay and self.base_delay * (2 ** (self.retries_5xx - 1)) < self.max_delay):
                try:
                    error_channel = await self.bot.get_or_fetch_channel(channels["bot_news"])
                    if error_channel:
                        text = get_phrases(error_channel.guild.id).get("statistic_message_loop", {}).get("api_error_notification", "MessageLoop got an error `{err_type}`. Delay: {delay} s.").format(err_type=err_type, delay=delay)
                        await error_channel.send(text)
                except Exception as e:
                    print(f"[ERROR main_loop_message] Critical error in handler while notifying about {err_type} error: {e}")
            
            self.retries_5xx += 1
            await asyncio.sleep(delay)
            self.main_loop.restart()
            return

        try:
            error_channel = await self.bot.get_or_fetch_channel(channels["bot_news"])
            text = get_phrases(error_channel.guild.id).get("statistic_message_loop", {}).get("general_error_notification", "Cycle MessageLoop issued an error: {error}").format(owner_id=owner_id, error=error)
            await error_channel.send(text)
        except Exception as e:
            print(f"[ERROR main_loop_message] Critical error in handler: {e}")


def setup(bot):
    bot.add_cog(MessageLoop(bot))
