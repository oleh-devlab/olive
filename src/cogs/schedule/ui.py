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
from modules.schedule_pagination import (
    MESSAGE_LIMIT,
    SchedulePage,
    build_notes,
    page_char_limit,
    paginate_days,
    trim_to_whole_lines,
)
from modules.schedule_provider import ScheduleProvider

logger = logging.getLogger(__name__)

provider = ScheduleProvider()

# A view holds 25 components: five pager buttons plus the "Skip rout.:" label
# leave room for nineteen routines.
MAX_SKIP_BUTTONS = 19

SKIP_PREFIX = "schedule_skip_"

# The frame is priced before the pages exist, so its page counter is measured as
# "1/1". This is the room a counter that grows to three digits needs on top.
PAGE_COUNTER_RESERVE = 8

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

        phrases = self.phrases(guild_id)

        # What a page can hold is what Discord's limit leaves once the frame is
        # paid for, and the frame is measured rather than guessed at: its format
        # string comes from `phrases.json` and an operator can rewrite it to any
        # length. Rendering it around an empty body is what prices it.
        source_header = self.header(guild_id)
        frame_cost = (
            len(self._render(phrases, "", 1, 1, perf_time, planning_days, status_text))
            + (len(source_header) + 2 if source_header else 0)
            + PAGE_COUNTER_RESERVE
        )

        notes = build_notes(skipped_tasks_ids, skipped_routines, frame_cost)
        schedule_pages = paginate_days(schedule_days, page_char_limit(frame_cost + len(notes))) or [
            SchedulePage(content=NO_ITEMS_TEXT)
        ]

        return [
            Page(
                content=self._render(
                    phrases,
                    schedule_page.content,
                    index + 1,
                    len(schedule_pages),
                    perf_time,
                    planning_days,
                    status_text,
                    notes,
                ),
                meta={"routine_ids": schedule_page.routine_ids, "date": schedule_page.date},
            )
            for index, schedule_page in enumerate(schedule_pages)
        ]

    def _render(
        self,
        phrases: dict,
        body: str,
        current_page: int,
        max_pages: int,
        perf_time: float,
        planning_days: int,
        status_text: str,
        notes: str = "",
    ) -> str:
        """One page's message text. Called with an empty body to price the frame."""
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

        content += notes

        # Only an over-long frame can get here — the body was measured against
        # it. Discord would refuse the edit outright and leave the channel on a
        # stale schedule, so the frame loses its tail instead.
        if len(content) > MESSAGE_LIMIT:
            logger.warning(
                "A schedule page is %s characters over Discord's limit; trimming it. "
                "Check `schedule_page_format` in phrases.json.",
                len(content) - MESSAGE_LIMIT,
            )
            content = trim_to_whole_lines(content)

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
