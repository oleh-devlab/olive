import disnake
from disnake.ext import commands
from datetime import datetime

import core.cache as cache
from core.utils import get_phrases
from core.time_utils import tz
import modules.schedule_formatter as auto_timetable


async def update_schedule_message(bot, channel_id, recalculate: bool = True):
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

    phrases = get_phrases().get("schedule", {})

    if recalculate or not state.get("pages"):
        if state.get("is_calculating", False):
            return
            
        state["is_calculating"] = True
        try:
            schedule_days, perf_time, planning_days, skipped_tasks_ids, skipped_routines, status_text = await auto_timetable.get_schedule_by_day(user_id)
            error_msg = None
        except Exception as e:
            print(f"[ERROR schedule_ui update_schedule_message] Error fetching schedule: {e}")
            schedule_days = []
            perf_time = 0.0
            planning_days = 0
            skipped_tasks_ids = []
            skipped_routines = []
            status_text = "ERROR"
            error_msg = f"Error fetching schedule: {e}"
        finally:
            state["is_calculating"] = False

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

                # If multiple pages for a day, append "(Part X)" to the headers
                if len(day_pages) > 1:
                    for i, p in enumerate(day_pages):
                        part_header = f"=== {day['date_str']} ({day['weekday']}) (Part {i+1}) ===\n"
                        p = p.replace(header, part_header, 1)
                        pages.append(p)
                else:
                    pages.extend(day_pages)
        
        state["pages"] = pages
        state["perf_time"] = perf_time
        state["planning_days"] = planning_days
        state["skipped_tasks_ids"] = skipped_tasks_ids
        state["skipped_routines"] = skipped_routines
        state["status_text"] = status_text
    else:
        pages = state.get("pages", ["No data."])
        perf_time = state.get("perf_time", 0.0)
        planning_days = state.get("planning_days", 0)
        skipped_tasks_ids = state.get("skipped_tasks_ids", [])
        skipped_routines = state.get("skipped_routines", [])
        status_text = state.get("status_text", "UNKNOWN")

    state["max_pages"] = len(pages)
    if current_page >= len(pages):
        current_page = len(pages) - 1
    if current_page < 0:
        current_page = 0

    state["current_page"] = current_page

    page_content = pages[current_page]

    schedule_format = phrases.get(
        "schedule_page_format",
        "`{formatted_time} UTC+2` | `Calculated in {perf_time:.4f}s`\n`Status: {status_text}`\n`The minimum planning horizon is {planning_days} days.`\n\n**Schedule (Page {current_page}/{max_pages}):**\n```text\n{page_content}\n```",
    )
    # Provide defaults if missing, but typically we have valid perf_time and planning_days
    schedule_content = schedule_format.format(
        formatted_time=formatted_time, 
        current_page=current_page + 1, 
        max_pages=len(pages), 
        page_content=page_content,
        planning_days=planning_days,
        perf_time=perf_time,
        status_text=status_text
    )

    if skipped_tasks_ids:
        schedule_content += f"\n\n*Tasks that didn't fit (IDs): {', '.join(map(str, skipped_tasks_ids))}*"
        
    if skipped_routines:
        prefix = "\n" if not skipped_tasks_ids else "\n"
        schedule_content += f"{prefix}*Skipped routines:*\n" + "\n".join(f"- {r}" for r in skipped_routines)

    view = SchedulePaginationView()
    prev_disabled = current_page <= 0
    next_disabled = current_page >= len(pages) - 1

    for child in view.children:
        if getattr(child, "custom_id", None) in ("schedule_prev_page", "schedule_first_page"):
            child.disabled = prev_disabled
        elif getattr(child, "custom_id", None) in ("schedule_next_page", "schedule_last_page"):
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

    async def change_page(self, interaction: disnake.MessageInteraction, delta: int = None, to_page: int = None):
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

        if to_page is not None:
            if to_page == -1:
                state["current_page"] = max(0, state.get("max_pages", 1) - 1)
            else:
                state["current_page"] = to_page
        elif delta is not None:
            state["current_page"] += delta

        # Only recalculate if it's a refresh (delta == 0)
        should_recalc = (delta == 0)
        
        if should_recalc and state.get("is_calculating", False):
            try:
                await interaction.followup.send("Please wait, the schedule is currently calculating.", ephemeral=True)
            except Exception:
                pass
            return
            
        await update_schedule_message(interaction.bot, channel_id, recalculate=should_recalc)

    @disnake.ui.button(label="⏮", style=disnake.ButtonStyle.primary, custom_id="schedule_first_page")
    async def first_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, to_page=0)

    @disnake.ui.button(label="◀", style=disnake.ButtonStyle.primary, custom_id="schedule_prev_page")
    async def prev_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=-1)

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.secondary, custom_id="schedule_refresh_page")
    async def refresh_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=0)

    @disnake.ui.button(label="▶", style=disnake.ButtonStyle.primary, custom_id="schedule_next_page")
    async def next_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=1)

    @disnake.ui.button(label="⏭", style=disnake.ButtonStyle.primary, custom_id="schedule_last_page")
    async def last_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, to_page=-1)


class ScheduleUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(SchedulePaginationView())

    @commands.Cog.listener("on_schedule_update")
    async def handle_schedule_update(self, channel_id: int):
        await update_schedule_message(self.bot, channel_id, recalculate=True)

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
            "pages": [],
            "perf_time": 0.0,
            "planning_days": 0,
            "skipped_tasks_ids": [],
            "skipped_routines": [],
            "status_text": "INIT",
            "is_calculating": False
        }

        await update_schedule_message(self.bot, channel.id, recalculate=True)


def setup(bot):
    bot.add_cog(ScheduleUI(bot))
