import asyncio
import aiohttp
import time
from datetime import datetime
import disnake
import traceback

from settings import channels
from core.time_utils import tz
from core.utils import get_phrases

class ResilientTaskHandler:
    def __init__(self, bot, task_loop, task_name: str, base_delay=5, max_delay=150):
        self.bot = bot
        self.task_loop = task_loop
        self.task_name = task_name
        
        self.retries_5xx = 0
        self.last_error_time = 0.0
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def handle_error(self, error):
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
            print(f"[{time_now}] {err_type} from Discord API in task '{self.task_name}'.")

            delay = min(self.max_delay, self.base_delay * (2 ** self.retries_5xx))
            print(f"[{self.task_name}] Attempt: {self.retries_5xx}. Delay before restart: {delay} seconds.")

            if (delay == self.base_delay) or (delay == self.max_delay and self.base_delay * (2 ** (self.retries_5xx - 1)) < self.max_delay):
                try:
                    error_channel = await self.bot.get_or_fetch_channel(channels["bot_news"])
                    if error_channel:
                        text = get_phrases(error_channel.guild.id).get("statistic_message_loop", {}).get(
                            "api_error_notification",
                            "Task `{task_name}` got an error `{err_type}`. Delay: {delay} s."
                        ).format(task_name=self.task_name, err_type=err_type, delay=delay)
                        await error_channel.send(text)
                except Exception as e:
                    print(f"[ERROR {self.task_name}] Critical error while notifying about {err_type}: {e}")
            
            self.retries_5xx += 1
            await asyncio.sleep(delay)
            self.task_loop.restart()
            return

        try:
            error_channel = await self.bot.get_or_fetch_channel(channels["bot_news"])
            text = get_phrases(error_channel.guild.id).get("statistic_message_loop", {}).get(
                "general_error_notification",
                "Task `{task_name}` issued an error: {error}"
            ).format(task_name=self.task_name, owner_id=self.bot.owner_id, error=error)
            await error_channel.send(text)
        except Exception as e:
            print(f"[ERROR {self.task_name}] Critical error in handler: {e}")