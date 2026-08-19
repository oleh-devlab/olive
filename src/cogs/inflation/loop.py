from disnake.ext import commands

from core.channel_loop import PersonalChannelLoopCog
from modules.inflation_provider import inflation_provider


class InflationMessageLoop(PersonalChannelLoopCog):
    """
    Slow safety net for the eternal report messages.

    Records change through commands, which dispatch `inflation_update`
    themselves, so this loop only exists to pick up what no interaction can
    announce: a new month starting, or freshly imported CPI data.
    """

    registry = inflation_provider.channels
    init_event = "inflation_init"
    update_event = "inflation_update"
    interval_setting = "inflation_loop_update_seconds"
    default_interval = 3600


class InflationServerMessageLoop(PersonalChannelLoopCog):
    """The same safety net for the public per-guild report messages."""

    registry = inflation_provider.server_channels
    init_event = "inflation_server_init"
    update_event = "inflation_server_update"
    interval_setting = "inflation_loop_update_seconds"
    default_interval = 3600


def setup(bot: commands.Bot) -> None:
    bot.add_cog(InflationMessageLoop(bot))
    bot.add_cog(InflationServerMessageLoop(bot))
