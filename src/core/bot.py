import disnake
from disnake.ext import commands
from datetime import datetime, timezone
from typing import Optional

import core.cache
import settings


class OliveBot(commands.Bot):
    # TODO: using reload_cogs

    def load_extension(self, name):
        try:
            clear_name = name.split(".", 1)[1]
            if clear_name in settings.cogs_blacklist:
                print(f'[COGS] Cog "{name}" in cogs blacklist.')
                return

            super().load_extension(name)

            current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            core.cache.active_cogs_list[name] = current_time

            print(f'[COGS] Cog "{name}" is loaded.')
        except Exception as e:
            print(f'[ERROR] Failed to load cog "{name}": {e}')

    def unload_extension(self, name):
        core.cache.active_cogs_list.pop(name, None)
        # NOTE: In some cases, the cog may be unloaded, but this function may not be triggered.

        try:
            super().unload_extension(name)

            print(f'[COGS] Cog "{name}" unloaded successfully.')
        except Exception as e:
            print(f'[ERROR] Failed to unload cog "{name}": {e}')

    async def get_or_fetch_channel(self, channel_id: int) -> Optional[disnake.abc.GuildChannel]:
        """
        Searches for the channel in the cache. If it isn't found, it sends a request to the API.
        Returns the channel object or `None` if the channel does not exist or is inaccessible.
        """

        # TODO: rate limit checking

        channel = self.get_channel(channel_id)

        if not channel:
            channel = await self.fetch_channel(channel_id)

        return channel
