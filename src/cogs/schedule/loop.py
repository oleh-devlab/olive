from disnake.ext import commands

from core import cache
from core.channel_loop import PersonalChannelLoopCog
from modules.schedule_provider import channels_registry


class ScheduleMessageLoop(PersonalChannelLoopCog):
    """
    Recomputes every user's schedule on a slow tick.

    Unlike the inflation report, a schedule goes stale on its own: time passes,
    deadlines approach and routines roll over, so this loop is the main way the
    message stays current rather than a safety net.
    """

    registry = channels_registry
    init_event = "schedule_init"
    # Not `schedule_update`, which says the user's data changed: a tick says
    # only that time has passed, and so has to force a new solve where a data
    # change can be answered from the last one when nothing actually changed.
    update_event = "schedule_tick"
    interval_setting = "schedule_loop_update_seconds"
    default_interval = 600

    async def prepare(self):
        """Map the tasks channels to their owners, for the cogs listening there."""
        for user_id, tasks_channel_id in self.registry.iter_management_channels():
            cache.tasks_channels[tasks_channel_id] = user_id


def update_interval(bot: commands.Bot) -> float | None:
    """How often the loop solves every schedule again, off the running loop itself.

    None while it is not loaded: then nothing solves again on its own, whatever
    `settings` says.
    """
    cog = bot.get_cog(ScheduleMessageLoop.__cog_name__)
    if cog is None:
        return None
    loop = cog.main_loop
    return (loop.hours or 0) * 3600 + (loop.minutes or 0) * 60 + (loop.seconds or 0)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(ScheduleMessageLoop(bot))
