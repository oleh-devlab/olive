import asyncio
from disnake.ext import commands, tasks
import traceback
import settings

from core.task_handler import ResilientTaskHandler
import core.cache as cache
from modules.schedule_provider import ScheduleProvider

provider = ScheduleProvider()


class ScheduleMessageLoop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.error_handler = ResilientTaskHandler(bot, self.main_loop, "Main_ScheduleMessageLoop")
        self.main_loop.start()

    def cog_unload(self):
        self.main_loop.cancel()

    @tasks.loop(seconds=getattr(settings, "schedule_loop_update_seconds", 600))
    async def main_loop(self):
        data = provider.load_channels()
        for user_id_str, info in data.items():
            channel_id = info.get("channel_id")
            if channel_id:
                try:
                    self.bot.dispatch("schedule_update", channel_id)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"[ERROR ScheduleMessageLoop main_loop] Error dispatching update: {e}")

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()

        try:
            channels = []

            # 1. Getting channels from JSON
            data = provider.load_channels()

            for user_id_str, info in data.items():
                channel_id = info.get("channel_id")
                user_id = int(user_id_str)
                tasks_channel_id = info.get("tasks_channel_id")

                if not hasattr(cache, "tasks_channels"):
                    cache.tasks_channels = {}
                if tasks_channel_id:
                    cache.tasks_channels[tasks_channel_id] = user_id

                try:
                    channel = await self.bot.get_or_fetch_channel(channel_id)
                    channels.append((channel, user_id))
                except Exception as e:
                    print(f"[before_main_loop WARNING] Not found channel {channel_id}: {e}")

            # 2. (Purge is now handled by EternalMessage in schedule_init)
            for channel, _ in channels:
                await asyncio.sleep(0.5)

            # 3. Sending initial messages and filling the list for future edits
            await asyncio.sleep(0.5)

            for channel, user_id in channels:
                try:
                    self.bot.dispatch("schedule_init", channel, user_id)
                    print(f"Dispatched schedule_init for channel {channel.id}.")
                except Exception as e:
                    print(f"[ERROR before_main_loop : dispatch init] Error for channel {channel.id}: {e}")

        except Exception as e:
            print(f"[ERROR in before_main_loop]: {e}")
            traceback.print_exc()

    @main_loop.error
    async def on_main_loop_error(self, error):
        await self.error_handler.handle_error(error)


def setup(bot):
    bot.add_cog(ScheduleMessageLoop(bot))
