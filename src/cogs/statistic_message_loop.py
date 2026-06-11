import asyncio
import aiohttp
from zoneinfo import ZoneInfo
from datetime import datetime
import time
from disnake.ext import commands, tasks
import traceback
import disnake

from settings import channels, owner_id

import core.cache


class MessageLoop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.channels = []
        self.messages = []

        self.last_embeds_dicts = []

        self.retries_503 = 0

        self.base_delay = 5
        self.max_delay = 150
        self.retries = 0

        self.last_error_time = 0.0

        self.main_loop.start()


    def cog_unload(self):
        self.main_loop.cancel()

    @tasks.loop(seconds=10)
    async def main_loop(self):
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        formatted_time = now.strftime('%d.%m.%Y %H:%M:%S')
        content = f"`{formatted_time} UTC+2`"

        valid_embeds = [emb for emb in core.cache.embeds_to_send.values() if emb is not None]
        # Колись зробити систему вимкнення ембедів через команду та БД і так далі

        # Compare current embeds to avoid unnecessary edits
        try:
            new_embeds_dicts = [e.to_dict() for e in valid_embeds]
        except Exception:
            print(f"Error converting new embeds to dicts for {content}.")
            new_embeds_dicts = []

        if self.last_embeds_dicts == new_embeds_dicts:
            # print(f"Embeds are the same, skipping edit for {content}.")
            return

        for message in self.messages:
            await message.edit(content=content, embeds=valid_embeds)
            await asyncio.sleep(0.5)
        self.last_embeds_dicts = new_embeds_dicts

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()

        self.channels = [self.bot.get_channel(channel_id) for channel_id in channels["statistic"]]
        for channel in self.channels:
            await asyncio.sleep(0.5)
            await channel.purge()
        await asyncio.sleep(0.5)
        text = core.cache.phrases.get("statistic_message_loop", {}).get("welcome_message", "Error with getting message for statistic channel.")
        for channel in self.channels:
            try:
                self.messages.append(await channel.send(text))
                print(f"Initial message sent to channel {channel.id} for MessageLoop.")
            except Exception as e:
                print(f"[ERROR before_main_loop : send initial message]: {e}")

    @main_loop.error
    async def on_main_loop_error(self, error):
        traceback.print_exc()
        is_server_error = isinstance(error, disnake.errors.HTTPException) and error.status >= 500
        is_connection_error = isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError))

        if is_server_error or is_connection_error:
            current_time = time.time()

            if current_time - self.last_error_time > 600:
                self.retries_503 = 0
                
            self.last_error_time = current_time

            err_type = f"HTTP {error.status}" if hasattr(error, 'status') else "Network error"
            time_now = datetime.now(ZoneInfo('Europe/Kyiv')).strftime('%d.%m.%Y %H:%M:%S')
            print(f"[{time_now}] {err_type} from Discord API.")

            delay = min(self.max_delay, self.base_delay * (2 ** self.retries_503))
            print(f"Attempt: {self.retries_503}. Delay before restart: {delay} seconds.")

            # Notify the channel only when we first reach the maximum delay or this is the first time
            if (delay == self.base_delay) or (delay == self.max_delay and self.base_delay * (2 ** (self.retries_503 - 1)) < self.max_delay):
                try:
                    error_channel = self.bot.get_channel(channels["bot_news"])
                    if error_channel:
                        text = core.cache.phrases.get("statistic_message_loop", {}).get("api_error_notification", "MessageLoop got an error `{err_type}`. Delay: {delay} s.").format(err_type=err_type, delay=delay)
                        await error_channel.send(text)
                except Exception as e:
                    print(f"[ERROR main_loop_message] Critical error in handler while notifying about {err_type} error: {e}")
            
            self.retries_503 += 1
            await asyncio.sleep(delay)
            self.main_loop.restart()
            return

        try:
            error_channel = self.bot.get_channel(channels["bot_news"])
            text = core.cache.phrases.get("statistic_message_loop", {}).get("general_error_notification", "Cycle MessageLoop issued an error: {error}").format(owner_id=owner_id, error=error)
            await error_channel.send(text)
        except Exception as e:
            print(f"[ERROR main_loop_message] Critical error in handler: {e}")


def setup(bot):
    bot.add_cog(MessageLoop(bot))
