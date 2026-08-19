import logging
from datetime import date as date_type
from datetime import datetime

import disnake
import settings
from disnake.ext import commands

import modules.schedule_formatter as auto_timetable
from core import cache
from core.paged_message import Page, PageSource, PaginationView, ensure_controller
from core.time_utils import tz
from core.utils import get_phrases
from modules.schedule_provider import ScheduleProvider

logger = logging.getLogger(__name__)

provider = ScheduleProvider()

# A day's blocks are split at this many characters, leaving room for the status
# header and the "didn't fit" notes below it.
PAGE_CHAR_LIMIT = 1500

# A view holds 25 components: five pager buttons plus the "Skip rout.:" label
# leave room for nineteen routines.
MAX_SKIP_BUTTONS = 19

SKIP_PREFIX = "schedule_skip_"

DEFAULT_PAGE_FORMAT = (
    "`{formatted_time} UTC+2` | `Calculated in {perf_time:.4f}s`\n"
    "`Status: {status_text}`\n"
    "`The minimum planning horizon is {planning_days} days.`\n"
    "*(Auto-updates every {update_mins} min)*\n\n"
    "**Schedule (Page {current_page}/{max_pages}):**\n```text\n{page_content}\n```"
)
NO_ITEMS_TEXT = (
    "You don't have any tasks or routines yet. " "Use `/task add` or `/routine add_flexible` to add your first items."
)


class SchedulePageSource(PageSource):
    """
    Pages of one user's computed schedule.

    Building a page means running the CP-SAT solver, which is why the controller
    refuses to start a second build while one is in flight.
    """

    # Both strings are load-bearing: `message_type` finds the existing message in
    # `webhooks_config.json`, `view_prefix` is the pager's custom_id prefix.
    message_type = "schedule"
    view_prefix = "schedule"
    phrases_section = "schedule"

    def phrases(self, guild_id: int | None) -> dict:
        return get_phrases(guild_id).get(self.phrases_section, {})

    async def build_pages(self, user_id: int, guild_id: int | None) -> list[Page]:
        (
            schedule_days,
            perf_time,
            planning_days,
            skipped_tasks_ids,
            skipped_routines,
            status_text,
        ) = await auto_timetable.get_schedule_by_day(user_id)

        bodies = self._split_days(schedule_days)
        if not bodies:
            bodies = [(NO_ITEMS_TEXT, set(), None)]

        return [
            Page(
                content=self._render(
                    self.phrases(guild_id),
                    body,
                    index + 1,
                    len(bodies),
                    perf_time,
                    planning_days,
                    status_text,
                    skipped_tasks_ids,
                    skipped_routines,
                ),
                meta={"routine_ids": routine_ids, "date": day_date},
            )
            for index, (body, routine_ids, day_date) in enumerate(bodies)
        ]

    def _split_days(self, schedule_days: list[dict]) -> list[tuple[str, set, date_type | None]]:
        """One entry per page: its text, the routines on it and the day it covers."""
        pages: list[tuple[str, set, date_type | None]] = []

        for day in schedule_days:
            header = f"=== {day['date_str']} ({day['weekday']}) ===\n"
            day_routine_ids = day.get("routine_ids", set())
            day_date = day.get("date_obj")

            day_pages = []
            current_blocks: list[str] = []
            current_len = len(header)

            for block in day["blocks"]:
                separator = 1 if current_blocks else 0

                if current_blocks and current_len + len(block) + separator > PAGE_CHAR_LIMIT:
                    day_pages.append(header + "\n".join(current_blocks))
                    current_blocks = [block]
                    current_len = len(header) + len(block)
                else:
                    current_blocks.append(block)
                    current_len += len(block) + separator

            if current_blocks:
                day_pages.append(header + "\n".join(current_blocks))

            # A day that needed more than one page says so in its header.
            if len(day_pages) > 1:
                for part, text in enumerate(day_pages, start=1):
                    part_header = f"=== {day['date_str']} ({day['weekday']}) (Part {part}) ===\n"
                    pages.append((text.replace(header, part_header, 1), day_routine_ids, day_date))
            else:
                pages.extend((text, day_routine_ids, day_date) for text in day_pages)

        return pages

    def _render(
        self,
        phrases: dict,
        body: str,
        current_page: int,
        max_pages: int,
        perf_time: float,
        planning_days: int,
        status_text: str,
        skipped_tasks_ids: list[int],
        skipped_routines: list[str],
    ) -> str:
        update_seconds = getattr(settings, "schedule_loop_update_seconds", None)

        content = phrases.get("schedule_page_format", DEFAULT_PAGE_FORMAT).format(
            formatted_time=datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S"),
            current_page=current_page,
            max_pages=max_pages,
            page_content=body,
            planning_days=planning_days,
            perf_time=perf_time,
            status_text=status_text,
            update_mins=str(update_seconds // 60) if update_seconds else "N/A",
        )

        if skipped_tasks_ids:
            content += f"\n\n*Tasks that didn't fit (IDs): {', '.join(map(str, skipped_tasks_ids))}*"

        if skipped_routines:
            prefix = "\n" if skipped_tasks_ids else "\n\n"
            content += f"{prefix}*Skipped routines:*\n" + "\n".join(f"- {routine}" for routine in skipped_routines)

        return content

    def welcome_text(self, guild_id: int | None) -> str:
        return self.phrases(guild_id).get("welcome_message", "Initializing schedule...")

    def error_page(self, guild_id: int | None, error: Exception) -> Page:
        return Page(content=f"Error fetching schedule: {error}")

    def extra_components(self, page: Page, page_index: int, guild_id: int | None) -> list[disnake.ui.Item]:
        """One button per routine on this page, to skip it for that day."""
        routine_ids = page.meta.get("routine_ids") or set()
        page_date = page.meta.get("date")

        if not routine_ids or page_date is None:
            return []

        date_str = page_date.isoformat()
        items: list[disnake.ui.Item] = [
            disnake.ui.Button(
                label="Skip rout.:",
                style=disnake.ButtonStyle.secondary,
                custom_id="schedule_skip_label",
                disabled=True,
            )
        ]
        items.extend(
            disnake.ui.Button(
                label=f"ID {routine_id}",
                style=disnake.ButtonStyle.secondary,
                custom_id=f"{SKIP_PREFIX}{routine_id}_{date_str}",
            )
            for routine_id in sorted(routine_ids)[:MAX_SKIP_BUTTONS]
        )

        return items


class ScheduleUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.source = SchedulePageSource()

    async def cog_load(self):
        # Persistent view: the pager keeps working across restarts. The skip
        # buttons cannot be registered this way — their custom_ids carry a
        # routine id and a date — so they go through the listener below.
        self.bot.add_view(PaginationView.for_source(SchedulePageSource))

    @commands.Cog.listener("on_button_click")
    async def handle_skip_button(self, interaction: disnake.MessageInteraction):
        custom_id = interaction.data.custom_id
        if not custom_id.startswith(SKIP_PREFIX):
            return

        # "schedule_skip_{routine_id}_{YYYY-MM-DD}"
        parts = custom_id.removeprefix(SKIP_PREFIX).split("_", 1)
        if len(parts) != 2:
            return

        try:
            routine_id = int(parts[0])
            resume_after_date = date_type.fromisoformat(parts[1])
        except (ValueError, IndexError):
            return

        controller = cache.channel_states.get(interaction.channel_id)
        if not controller:
            await interaction.response.send_message("State not found, wait for update.", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except Exception as e:
            logger.debug("Failed to defer skip-routine interaction: %s", e)

        user_id = controller.user_id
        current_routine = provider.get_routine(user_id, routine_id)
        if not current_routine:
            await self.send_followup(interaction, f"Routine {routine_id} not found.")
            return

        current_resume = getattr(current_routine, "resume_after", None)
        if current_resume and current_resume > resume_after_date:
            logger.info(
                f"[User {user_id}] Routine {routine_id} skip ignored in UI: "
                f"current resume_after ({current_resume}) is later than requested ({resume_after_date})"
            )
            await self.send_followup(
                interaction, f"Routine is already skipped until {current_resume.strftime('%d.%m.%Y')}."
            )
            return

        provider.skip_routine(user_id, routine_id, resume_after_date)

        current_view = controller.last_view
        if not current_view:
            await self.send_followup(interaction, "View state not found, wait for update.")
            return

        # The schedule is not recomputed here — the next tick does that. Only the
        # button that was just used is greyed out, so it cannot be pressed twice.
        for child in current_view.children:
            if getattr(child, "custom_id", None) == custom_id:
                child.disabled = True
                break

        try:
            await interaction.edit_original_response(view=current_view)
            await interaction.followup.send(
                f"Routine {routine_id} skipped. The schedule will recalculate shortly.", ephemeral=True
            )
        except Exception as e:
            logger.debug("Failed to update message after skipping a routine: %s", e)

    async def send_followup(self, interaction: disnake.MessageInteraction, text: str):
        try:
            await interaction.followup.send(text, ephemeral=True)
        except Exception as e:
            logger.debug("Failed to send followup '%s': %s", text, e)

    @commands.Cog.listener("on_schedule_update")
    async def handle_schedule_update(self, channel_id: int):
        controller = cache.channel_states.get(channel_id)
        if not controller:
            logger.debug("No controller for channel %s yet; skipping update.", channel_id)
            return

        await controller.refresh(rebuild=True)

    @commands.Cog.listener("on_schedule_init")
    async def handle_schedule_init(self, channel: disnake.TextChannel, user_id: int):
        await ensure_controller(self.bot, channel.id, user_id, self.source)


def setup(bot):
    bot.add_cog(ScheduleUI(bot))
