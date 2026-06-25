from disnake.ext import commands, tasks
from datetime import datetime, timezone
import psutil
import disnake

import core.cache
from core.task_handler import ResilientTaskHandler
from core.utils import u_decline, format_embed_data, get_phrases

async def get_memory_info():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        'memory_total_gib': round(mem.total / (1024 ** 3), 2),
        'memory_used_gib': round(mem.used / (1024 ** 3), 2),
        'memory_percent': mem.percent,
        'swap_total_gib': round(swap.total / (1024 ** 3), 2),
        'swap_used_gib': round(swap.used / (1024 ** 3), 2),
        'swap_percent': swap.percent
    }

class Hosting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        self.error_handler = ResilientTaskHandler(bot, self.hosting_loop, "HostingLoop")

        self.hosting_loop.start()

    def cog_unload(self):
        self.hosting_loop.stop()

    async def get_taimer_embed(self):
        test_datetime = datetime(2025, 5, 14, 0, 0, 0)
        sleep_hours_per_day = 8.5
        
        now = datetime.now(timezone.utc)
        delta = test_datetime - now
        
        total_seconds = int(delta.total_seconds())
        total_days = delta.days
        total_hours = total_seconds // 3600

        # --- Without sleep ---
        weeks = total_days // 7
        days = total_days % 7
        hours = (total_seconds % (24 * 3600)) // 3600
        
        
        active_hours_total = total_hours - int(total_days * sleep_hours_per_day)
        active_days_total = active_hours_total // 24
        active_weeks = active_days_total // 7
        active_days = active_days_total % 7
        active_hours = active_hours_total % 24

        # For now, for the Ukrainian language          
        weeks_start = await u_decline(weeks, ['тиждень', 'тижні', 'тижнів'])
        days_start = await u_decline(days, ['день', 'дні', 'днів'])
        hours_start = await u_decline(hours, ['година', 'години', 'годин'])
        total_days_start = await u_decline(total_days, ['день', 'дні', 'днів'])
        total_hours_start = await u_decline(total_hours, ['година', 'години', 'годин'])
        
        active_weeks_start = await u_decline(active_weeks, ['тиждень', 'тижні', 'тижнів'])
        active_days_start = await u_decline(active_days, ['день', 'дні', 'днів'])
        active_hours_start = await u_decline(active_hours, ['година', 'години', 'годин'])
        active_days_total_start = await u_decline(active_days_total, ['день', 'дні', 'днів'])
        active_hours_total_start = await u_decline(active_hours_total, ['година', 'години', 'годин'])

        raw_embed_data = get_phrases().get("hosting_embed", {}).get("nmt_taimer_embed_data", { "title": "NMT" })
        formatted_embed_data = format_embed_data(raw_embed_data, 
                                                 weeks_start=weeks_start, days_start=days_start, hours_start=hours_start,
                                                 total_days_start=total_days_start, total_hours_start=total_hours_start,
                                                 active_weeks_start=active_weeks_start, active_days_start=active_days_start, active_hours_start=active_hours_start,
                                                 active_days_total_start=active_days_total_start, active_hours_total_start=active_hours_total_start)
        
        embed = disnake.Embed.from_dict(formatted_embed_data)

        return embed
    
    @tasks.loop(seconds=10)
    async def hosting_loop(self):
        memory_info = await get_memory_info()

        total_used = memory_info['swap_used_gib'] + memory_info['memory_used_gib']
        total_total = memory_info['swap_total_gib'] + memory_info['memory_total_gib']
        total_percent = (100 * (total_used / total_total)) if total_total > 0 else 0
        
        raw_embed_data = get_phrases().get("hosting_embed", {}).get("server_embed_data", { "title": "Сервер" })
        formatted_embed_data = format_embed_data(raw_embed_data, 
                                                 memory_used_gib=memory_info['memory_used_gib'], memory_total_gib=memory_info['memory_total_gib'], memory_percent=memory_info['memory_percent'],
                                                 swap_used_gib=memory_info['swap_used_gib'], swap_total_gib=memory_info['swap_total_gib'], swap_percent=memory_info['swap_percent'],
                                                 total_used=total_used, total_total=total_total, total_percent=total_percent)
        
        embed0 = disnake.Embed.from_dict(formatted_embed_data)

        core.cache.embeds_to_send["server_load"] = embed0

    @hosting_loop.error
    async def on_ram_error(self, error):
        await self.error_handler.handle_error(error)

def setup(bot):
    bot.add_cog(Hosting(bot))