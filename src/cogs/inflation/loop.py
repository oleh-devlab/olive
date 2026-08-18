import asyncio
import logging

import settings
from disnake.ext import commands, tasks

from core.task_handler import ResilientTaskHandler
from modules.inflation_provider import inflation_provider

logger = logging.getLogger(__name__)


class InflationMessageLoop(commands.Cog):
    """
    Slow safety net for the eternal report messages.

    Records change through commands, which dispatch `inflation_update`
    themselves, so this loop only exists to pick up what no interaction can
    announce: a new month starting, or freshly imported CPI data.
    """

    def __init__(self, bot):
        self.bot = bot
        self.error_handler = ResilientTaskHandler(bot, self.main_loop, "Main_InflationMessageLoop")

        self.main_loop.change_interval(seconds=getattr(settings, "inflation_loop_update_seconds", 3600))
        self.main_loop.start()

    def cog_unload(self):
        self.main_loop.cancel()

    # The interval here is a placeholder — `__init__` overrides it per instance.
    @tasks.loop(seconds=3600)
    async def main_loop(self):
        for info in inflation_provider.load_channels().values():
            channel_id = info.get("report_channel_id")
            if not channel_id:
                continue

            self.bot.dispatch("inflation_update", channel_id)
            await asyncio.sleep(0.5)

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()

        for user_id_str, info in inflation_provider.load_channels().items():
            channel_id = info.get("report_channel_id")
            if not channel_id:
                continue

            try:
                channel = await self.bot.get_or_fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"Inflation report channel {channel_id} not found: {e}")
                continue

            if not channel:
                continue

            self.bot.dispatch("inflation_init", channel, int(user_id_str))
            logger.info(f"Dispatched inflation_init for channel {channel_id}.")
            await asyncio.sleep(0.5)

    @main_loop.error
    async def on_main_loop_error(self, error):
        await self.error_handler.handle_error(error)


def setup(bot):
    bot.add_cog(InflationMessageLoop(bot))
