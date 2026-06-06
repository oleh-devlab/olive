from disnake.ext import commands
from datetime import datetime, timezone

import core.cache

class OliveBot (commands.Bot):
    # TODO reload_cogs
    
    def load_extension(self, name):
        try:
            super().load_extension(name)

            current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            core.cache.active_cogs_list[name] = current_time

            print(f'[COGS] Cog "{name}" is loaded.')
        except Exception as e:
            print(f'[ERROR] Failed to load cog "{name}": {e}')

    def unload_extension(self, name):
        core.cache.active_cogs_list.pop(name, None)
        # ! In some cases, the cog may be unloaded, but this function may not be triggered.

        try:
            super().unload_extension(name)

            print(f'[COGS] Cog "{name}" unloaded successfully.')
        except Exception as e:
            print(f'[ERROR] Failed to unload cog "{name}": {e}')
