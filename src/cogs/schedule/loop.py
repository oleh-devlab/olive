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
    update_event = "schedule_update"
    interval_setting = "schedule_loop_update_seconds"
    default_interval = 600

    async def prepare(self):
        """Map the tasks channels to their owners, for the cogs listening there."""
        for user_id, tasks_channel_id in self.registry.iter_management_channels():
            cache.tasks_channels[tasks_channel_id] = user_id


def setup(bot: commands.Bot) -> None:
    bot.add_cog(ScheduleMessageLoop(bot))
