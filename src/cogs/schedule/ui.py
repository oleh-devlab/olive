import disnake
from disnake.ext import commands
from datetime import datetime

import core.cache as cache
from core.utils import get_phrases
from core.time_utils import tz
import modules.schedule_formatter as auto_timetable


async def update_schedule_message(bot, channel_id):
    state = cache.schedule_states.get(channel_id)
    if not state:
        return

    user_id = state["user_id"]
    msg = state["message"]
    current_page = state["current_page"]

    now = datetime.now(tz)
    formatted_time = now.strftime("%d.%m.%Y %H:%M:%S")

    channel = bot.get_channel(channel_id)
    if not channel:
        cache.schedule_states.pop(channel_id, None)
        return
    guild_id = channel.guild.id
    phrases = get_phrases(guild_id).get("schedule", {})

    try:
        schedule_days = await auto_timetable.get_schedule_by_day(user_id)
        error_msg = None
    except Exception as e:
        print(f"[ERROR schedule_ui update_schedule_message] Error fetching schedule: {e}")
        schedule_days = []
        error_msg = f"Error fetching schedule: {e}"

    pages = []

    if error_msg:
        pages = [error_msg]
    elif not schedule_days:
        pages = ["You don't have any tasks or routines yet. Use `/task add` or `/routine add_flexible` to add your first items."]
    else:
        for day in schedule_days:
            header = f"=== {day['date_str']} ({day['weekday']}) ===\n"
            blocks = day["blocks"]

            # UX: We want the tasks inside the day reversed (bottom to top chronological)
            blocks_reversed = list(reversed(blocks))

            day_pages = []
            current_page_blocks = []
            current_len = len(header)

            for block in blocks_reversed:
                block_len = len(block)
                if current_len + block_len + (1 if current_len > len(header) else 0) > 1500 and current_page_blocks:
                    day_pages.append(header + "\n".join(current_page_blocks))
                    current_page_blocks = [block]
                    current_len = len(header) + block_len
                else:
                    current_page_blocks.append(block)
                    current_len += block_len + (1 if current_len > len(header) else 0)

            if current_page_blocks:
                day_pages.append(header + "\n".join(current_page_blocks))

            # If multiple pages for a day, append "(Частина X)" to the headers
            if len(day_pages) > 1:
                for i, p in enumerate(day_pages):
                    part_header = f"=== {day['date_str']} ({day['weekday']}) (Частина {i+1}) ===\n"
                    p = p.replace(header, part_header, 1)
                    pages.append(p)
            else:
                pages.extend(day_pages)

    state["max_pages"] = len(pages)
    if current_page >= len(pages):
        current_page = len(pages) - 1
    if current_page < 0:
        current_page = 0

    state["current_page"] = current_page

    page_content = pages[current_page]

    schedule_format = phrases.get(
        "schedule_page_format",
        "`{formatted_time} UTC+2`\n\n**Schedule (Page {current_page}/{max_pages}):**\n```text\n{page_content}\n```",
    )
    schedule_content = schedule_format.format(
        formatted_time=formatted_time, current_page=current_page + 1, max_pages=len(pages), page_content=page_content
    )

    view = SchedulePaginationView()
    prev_disabled = current_page <= 0
    next_disabled = current_page >= len(pages) - 1

    for child in view.children:
        if getattr(child, "custom_id", None) == "schedule_prev_page":
            child.disabled = prev_disabled
        elif getattr(child, "custom_id", None) == "schedule_next_page":
            child.disabled = next_disabled

    view_state = (prev_disabled, next_disabled)

    if state.get("last_content") != schedule_content or state.get("last_view_state") != view_state:
        try:
            await msg.edit(content=schedule_content, view=view)
            state["last_content"] = schedule_content
            state["last_view_state"] = view_state
        except Exception as e:
            print(f"[ERROR schedule_ui update_schedule_message] Error editing message: {e}")


class SchedulePaginationView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def change_page(self, interaction: disnake.MessageInteraction, delta: int):
        channel_id = interaction.channel_id
        phrases = get_phrases(interaction.guild.id if interaction.guild else None).get("schedule", {})

        if channel_id not in cache.schedule_states:
            await interaction.response.send_message(
                phrases.get("state_not_found", "State not found, wait for update."), ephemeral=True
            )
            return

        state = cache.schedule_states[channel_id]

        try:
            await interaction.response.defer()
        except Exception:
            pass

        state["current_page"] += delta
        await update_schedule_message(interaction.bot, channel_id)

    @disnake.ui.button(label="◀", style=disnake.ButtonStyle.primary, custom_id="schedule_prev_page")
    async def prev_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, -1)

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.secondary, custom_id="schedule_refresh_page")
    async def refresh_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, 0)

    @disnake.ui.button(label="▶", style=disnake.ButtonStyle.primary, custom_id="schedule_next_page")
    async def next_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, 1)


class ScheduleUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(SchedulePaginationView())

    @commands.Cog.listener("on_schedule_update")
    async def handle_schedule_update(self, channel_id: int):
        await update_schedule_message(self.bot, channel_id)

    @commands.Cog.listener("on_schedule_init")
    async def handle_schedule_init(self, channel: disnake.TextChannel, user_id: int):
        guild_id = channel.guild.id if channel.guild else None
        phrases = get_phrases(guild_id).get("schedule", {})
        text = phrases.get("welcome_message", "Initializing schedule...")

        view = SchedulePaginationView()
        msg = await channel.send(text, view=view)

        cache.schedule_states[channel.id] = {
            "user_id": user_id,
            "message": msg,
            "current_page": 0,
            "max_pages": 1,
            "last_content": "",
            "last_view_state": None,
        }

        await update_schedule_message(self.bot, channel.id)


def setup(bot):
    bot.add_cog(ScheduleUI(bot))
