import logging
from datetime import datetime

import disnake
import settings
from disnake.ext import commands

from core import cache
from core.eternal_message import EternalMessage
from core.time_utils import tz
from core.utils import get_phrases
from modules.inflation_report import build_report, render_page

logger = logging.getLogger(__name__)


def get_inflation_phrases(guild_id: int | None = None) -> dict:
    return get_phrases(guild_id).get("inflation", {})


async def update_report_message(
    bot,
    channel_id: int,
    recalculate: bool = True,
    interaction: disnake.MessageInteraction | None = None,
):
    """
    Rebuild the eternal report message for one channel.

    Unlike the schedule, building a report is cheap (no solver), so there is no
    "is calculating" guard — but the message is still only edited when its
    content actually changed, to keep the API calls down.
    """
    state = cache.inflation_states.get(channel_id)
    if not state:
        return

    em = state.get("em")
    if not em:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        cache.inflation_states.pop(channel_id, None)
        return

    guild_id = channel.guild.id if channel.guild else None
    phrases = get_inflation_phrases(guild_id)

    if recalculate or state.get("summary") is None:
        try:
            summary, pages = build_report(state["user_id"], guild_id)
            failed = False
        except Exception as e:
            logger.error(f"Error building inflation report for channel {channel_id}: {e}")
            summary = phrases.get("report_error", "Failed to build the report: {error}").format(error=e)
            pages = []
            failed = True

        state["summary"] = summary
        state["pages"] = pages
        state["failed"] = failed

    summary = state["summary"]
    pages = state["pages"]

    current_page = min(max(state.get("current_page", 0), 0), max(len(pages) - 1, 0)) # (!)
    state["current_page"] = current_page
    state["max_pages"] = len(pages)

    update_seconds = getattr(settings, "inflation_loop_update_seconds", 3600)
    header = phrases.get("updated_header", "`{formatted_time}` *(auto-updates every {update_mins} min)*").format(
        formatted_time=datetime.now(tz).strftime("%d.%m.%Y %H:%M:%S"),
        update_mins=max(update_seconds // 60, 1),
    )
    # On failure `summary` already carries the error, so skip the "no records yet" hint.
    body = summary if state.get("failed") else render_page(summary, pages, current_page, guild_id)
    content = f"{header}\n\n{body}"

    view = InflationPaginationView()
    prev_disabled = current_page <= 0
    next_disabled = current_page >= len(pages) - 1

    for child in view.children:
        if getattr(child, "custom_id", None) in ("inflation_first_page", "inflation_prev_page"):
            child.disabled = prev_disabled
        elif getattr(child, "custom_id", None) in ("inflation_next_page", "inflation_last_page"):
            child.disabled = next_disabled

    view_state = (prev_disabled, next_disabled)

    if state.get("last_content") == content and state.get("last_view_state") == view_state:
        return

    try:
        if interaction:
            await interaction.edit_original_response(content=content, view=view)
        else:
            fallback_text = phrases.get("welcome_message", "Initializing the inflation report...")
            await em.update(fallback_kwargs={"content": fallback_text, "view": view}, content=content, view=view)

        state["last_content"] = content
        state["last_view_state"] = view_state
    except Exception as e:
        logger.error(f"Error editing inflation message in {channel_id}: {e}")


class InflationPaginationView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def change_page(
        self, interaction: disnake.MessageInteraction, delta: int | None = None, to_page: int | None = None
    ):
        channel_id = interaction.channel_id
        phrases = get_inflation_phrases(interaction.guild.id if interaction.guild else None)

        state = cache.inflation_states.get(channel_id)
        if not state:
            await interaction.response.send_message(
                phrases.get("state_not_found", "State not found, wait for update."), ephemeral=True
            )
            return

        try:
            await interaction.response.defer()
        except Exception as e:
            logger.debug("Failed to defer inflation pagination interaction: %s", e)

        if to_page is not None:
            state["current_page"] = max(0, state.get("max_pages", 1) - 1) if to_page == -1 else to_page
        elif delta is not None:
            state["current_page"] += delta

        # Only the refresh button rereads the records from disk.
        await update_report_message(interaction.bot, channel_id, recalculate=delta == 0, interaction=interaction)

    @disnake.ui.button(label="⏮", style=disnake.ButtonStyle.primary, custom_id="inflation_first_page")
    async def first_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, to_page=0)

    @disnake.ui.button(label="◀", style=disnake.ButtonStyle.primary, custom_id="inflation_prev_page")
    async def prev_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=-1)

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.secondary, custom_id="inflation_refresh_page")
    async def refresh_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=0)

    @disnake.ui.button(label="▶", style=disnake.ButtonStyle.primary, custom_id="inflation_next_page")
    async def next_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, delta=1)

    @disnake.ui.button(label="⏭", style=disnake.ButtonStyle.primary, custom_id="inflation_last_page")
    async def last_page(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await self.change_page(interaction, to_page=-1)


class InflationUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # Persistent view: the buttons keep working across restarts.
        self.bot.add_view(InflationPaginationView())

    @commands.Cog.listener("on_inflation_update")
    async def handle_inflation_update(self, channel_id: int):
        await update_report_message(self.bot, channel_id, recalculate=True)

    @commands.Cog.listener("on_inflation_init")
    async def handle_inflation_init(self, channel: disnake.TextChannel, user_id: int):
        guild_id = channel.guild.id if channel.guild else None
        phrases = get_inflation_phrases(guild_id)
        text = phrases.get("welcome_message", "Initializing the inflation report...")

        em = EternalMessage(self.bot, channel.id, "inflation")
        success = await em.init_message({"content": text, "view": InflationPaginationView()}, purge_on_recreate=True)
        if not success:
            logger.error(f"Failed to initialize eternal inflation message for channel {channel.id}")
            return

        cache.inflation_states[channel.id] = {
            "user_id": user_id,
            "em": em,
            "current_page": 0,
            "max_pages": 1,
            "summary": None,
            "pages": [],
            "failed": False,
            "last_content": "",
            "last_view_state": None,
        }

        await update_report_message(self.bot, channel.id, recalculate=True)


def setup(bot):
    bot.add_cog(InflationUI(bot))
