import disnake
from disnake.ext import commands

import settings
import modules.automatic_timetable as auto_timetable

class AutoSchedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def get_test_schedule(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)

        ID = inter.author.id
        try:
            schedule = await auto_timetable.get_schedule(ID)
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")
            return

        await inter.edit_original_response(f"Schedule:\n```{schedule}```")

def setup(bot):
    bot.add_cog(AutoSchedule(bot))
