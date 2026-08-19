"""Base cog for the loops that keep personal channel messages alive.

Same idea as `BaseEmbedCog`: the subclass declares where its channels come from
and which events to fire, and the interval, the startup pass and the error
handling are inherited.
"""

import asyncio
import logging

import settings
from disnake.ext import commands, tasks

from core.personal_channels import PersonalChannelRegistry
from core.task_handler import ResilientTaskHandler

logger = logging.getLogger(__name__)


class PersonalChannelLoopCog(commands.Cog):
    """
    Dispatches an init event for every registered channel on startup, then an
    update event for each of them on every tick.

    The pause between dispatches is deliberate: each one ends in a message edit,
    and a burst of them across many users is a good way to meet a rate limit.
    """

    registry: PersonalChannelRegistry | None = None
    init_event: str = ""
    update_event: str = ""
    interval_setting: str = ""
    default_interval: int = 3600
    dispatch_pause: float = 0.5

    def __init__(self, bot):
        if self.registry is None or not (self.init_event and self.update_event):
            raise ValueError(f"{type(self).__name__} must set registry, init_event and update_event.")

        self.bot = bot
        self.error_handler = ResilientTaskHandler(bot, self.main_loop, type(self).__name__)

        # `tasks.Loop` is a descriptor that clones itself on first attribute
        # access, so the interval stays bound to this instance only.
        self.main_loop.change_interval(seconds=getattr(settings, self.interval_setting, self.default_interval))
        self.main_loop.start()

    def cog_unload(self):
        self.main_loop.cancel()

    # The interval here is a placeholder — `__init__` overrides it per instance.
    @tasks.loop(seconds=3600)
    async def main_loop(self):
        for _, channel_id in self.registry.iter_display_channels():
            self.bot.dispatch(self.update_event, channel_id)
            await asyncio.sleep(self.dispatch_pause)

    async def prepare(self):
        """Hook for whatever a module needs done before the first dispatch."""

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()
        await self.prepare()

        for user_id, channel_id in self.registry.iter_display_channels():
            try:
                channel = await self.bot.get_or_fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"Channel {channel_id} not found: {e}")
                continue

            if not channel:
                continue

            self.bot.dispatch(self.init_event, channel, user_id)
            logger.info(f"Dispatched {self.init_event} for channel {channel_id}.")
            await asyncio.sleep(self.dispatch_pause)

    @main_loop.error
    async def on_main_loop_error(self, error):
        await self.error_handler.handle_error(error)
