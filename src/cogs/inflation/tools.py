import asyncio
import datetime
import logging
import traceback

import disnake
import settings
from disnake.ext import commands

from core import cache, utils
from core.utils import format_phrase
from core.personal_channels import ChannelSetupError, create_channel_pair
from modules.inflation_calculator.modules.exceptions import InflationCalculatorError, ValidationError
from modules.inflation_provider import inflation_provider
from modules.inflation_formatter import format_money
from modules.inflation_report import build_rates_warning, build_report, get_currency, render_page

logger = logging.getLogger(__name__)

# Read once at import time, like the schedule cog: command descriptions are
# registered with Discord on load, so `/reload_phrases` cannot change them.
phrases_cmd = utils.get_phrases().get("inflation_cmd", {})

DATE_FORMAT = "%d.%m.%Y"


def get_phrases(inter: disnake.ApplicationCommandInteraction) -> dict:
    return utils.get_phrases(inter.guild.id if inter.guild else None).get("inflation", {})


async def report_error(inter: disnake.ApplicationCommandInteraction, phrases: dict, error: Exception, action: str):
    """Turn a calculator exception into a user-facing message."""
    if isinstance(error, ValidationError):
        text = format_phrase(phrases, "validation_error", "Validation error: {error}", error=error)
    elif isinstance(error, InflationCalculatorError):
        text = format_phrase(phrases, "calculator_error", "Inflation calculator error: {error}", error=error)
    else:
        logger.error(f"Unexpected error in inflation {action}: {traceback.format_exc()}")
        text = phrases.get("unexpected_error", "An unexpected error occurred. Please notify the administrator.")

    await inter.edit_original_response(content=text)


class InflationTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def notify_update(self, user_id: int):
        """Refresh the user's eternal report message, if they have a report channel."""
        channel_id = inflation_provider.get_report_channel_id(user_id)
        if channel_id:
            self.bot.dispatch("inflation_update", channel_id)

    @commands.slash_command(
        name="inflation",
        description=phrases_cmd.get("cmd_inflation_desc", "Commands for inflation calculator"),
        test_guilds=settings.guilds,
    )
    async def inflation(self, inter: disnake.ApplicationCommandInteraction):
        """Base command for inflation calculator"""
        pass

    @inflation.sub_command(
        name="add",
        description=phrases_cmd.get("cmd_add_desc", "Add a new record to calculate its inflation later"),
    )
    async def add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        amount: str = commands.Param(description=phrases_cmd.get("param_amount", "Amount of money (e.g. 5000)")),
        date_str: str = commands.Param(description=phrases_cmd.get("param_date", "Date in DD.MM.YYYY format")),
        comment: str = commands.Param(
            default="", description=phrases_cmd.get("param_comment", "Optional comment for this record")
        ),
    ):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            date_obj = datetime.datetime.strptime(date_str, DATE_FORMAT).date()
        except ValueError:
            await inter.edit_original_response(
                content=phrases.get("invalid_date", "Invalid date format. Please use DD.MM.YYYY")
            )
            return

        try:
            record = inflation_provider.add_record(inter.author.id, amount, date_obj, comment)
        except Exception as e:
            await report_error(inter, phrases, e, "add")
            return

        self.notify_update(inter.author.id)
        await inter.edit_original_response(
            content=format_phrase(
                phrases, "record_added", "Record added successfully! ID: {record_id}", record_id=record.get("id")
            )
        )

    @inflation.sub_command(
        name="delete",
        description=phrases_cmd.get("cmd_delete_desc", "Delete a record by its ID"),
    )
    async def delete(
        self,
        inter: disnake.ApplicationCommandInteraction,
        record_id: int = commands.Param(description=phrases_cmd.get("param_record_id", "ID of the record to delete")),
    ):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            record = inflation_provider.delete_record(inter.author.id, record_id)
        except Exception as e:
            await report_error(inter, phrases, e, "delete")
            return

        self.notify_update(inter.author.id)
        currency = get_currency(inter.guild.id if inter.guild else None)
        await inter.edit_original_response(
            content=format_phrase(
                phrases,
                "record_deleted",
                "Record deleted successfully! (Amount: {amount})",
                amount=format_money(record.get("amount", 0), currency),
            )
        )

    @inflation.sub_command(
        name="report",
        description=phrases_cmd.get("cmd_report_desc", "Get your personal inflation report"),
    )
    async def report(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            summary, pages = build_report(inter.author.id, inter.guild.id if inter.guild else None)
        except Exception as e:
            await report_error(inter, phrases, e, "report")
            return

        content = render_page(summary, pages, 0, inter.guild.id if inter.guild else None)

        if len(pages) > 1:
            content += "\n" + phrases.get(
                "more_pages_hint",
                "*Only the first page is shown here. Use `/inflation_channel create` for a paginated report.*",
            )

        await inter.edit_original_response(content=content)

    @commands.slash_command(
        name="inflation_channel",
        description=phrases_cmd.get("cmd_inflation_channel_desc", "Manage personal inflation channels"),
        test_guilds=settings.guilds,
    )
    async def inflation_channel(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @inflation_channel.sub_command(
        name="create",
        description=phrases_cmd.get("cmd_inflation_channel_create_desc", "Create personal inflation channels"),
    )
    async def inflation_channel_create(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            report_channel, management_channel = await create_channel_pair(
                inter,
                registry=inflation_provider.channels,
                categories=getattr(settings, "inflation_categories", {}),
                max_per_guild=getattr(settings, "inflation_max_channels_per_guild", 5),
                display_name=f"inflation-{inter.author.display_name}",
                management_name=f"records-{inter.author.display_name}",
                reason="Automatic creation of inflation channels",
            )
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        self.bot.dispatch("inflation_init", report_channel, inter.author.id)

        await inter.edit_original_response(
            content=format_phrase(
                phrases,
                "channels_created",
                "Channels successfully created:\n- Report {report_channel}\n- Records {management_channel}",
                report_channel=report_channel.mention,
                management_channel=management_channel.mention,
            )
        )

        await management_channel.send(
            format_phrase(
                phrases,
                "privacy_warning",
                "{user_mention}, this channel is for your inflation commands. Command replies here are private "
                "(ephemeral), but the report channel is visible to anyone who can read it.",
                user_mention=f"<@{inter.author.id}>",
            )
        )

    @inflation_channel.sub_command(
        name="delete",
        description=phrases_cmd.get("cmd_inflation_channel_delete_desc", "Delete your inflation channels"),
    )
    async def inflation_channel_delete(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        removed = inflation_provider.channels.remove(inter.author.id)
        if not removed:
            await inter.edit_original_response(
                content=phrases.get("no_channels", "You do not have inflation channels.")
            )
            return

        report_channel_id = removed.get("report_channel_id")
        cache.channel_states.pop(report_channel_id, None)

        # Records stay on disk; only the Discord channels go away.
        await inter.edit_original_response(
            content=phrases.get("channels_deleted", "Your inflation channels are being deleted. Records are kept.")
        )

        for channel_id in (report_channel_id, removed.get("management_channel_id")):
            if not channel_id:
                continue
            try:
                channel = await self.bot.get_or_fetch_channel(channel_id)
                if channel:
                    await channel.delete(reason="Inflation channels removed by their owner")
            except Exception as e:
                logger.warning(f"Could not delete inflation channel {channel_id}: {e}")

    async def notify_all_updates(self):
        """Refresh every report message: a rate change moves everyone's numbers."""
        for _, channel_id in inflation_provider.channels.iter_display_channels():
            self.bot.dispatch("inflation_update", channel_id)
            await asyncio.sleep(0.5)

    @commands.slash_command(
        name="inflation_rates",
        description=phrases_cmd.get("cmd_inflation_rates_desc", "Inspect and edit monthly CPI data"),
        test_guilds=settings.guilds,
    )
    async def inflation_rates(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @inflation_rates.sub_command(
        name="status",
        description=phrases_cmd.get("cmd_inflation_rates_status_desc", "Show what CPI data the bot has"),
    )
    async def inflation_rates_status(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            rates = inflation_provider.get_rates()
        except Exception as e:
            await report_error(inter, phrases, e, "rates status")
            return

        guild_id = inter.guild.id if inter.guild else None
        if not rates:
            await inter.edit_original_response(content=build_rates_warning(guild_id))
            return

        months = sorted(rates)
        content = format_phrase(
            phrases,
            "rates_status",
            "CPI data: `{count}` months, from `{oldest}` to `{newest}`.",
            count=len(months),
            oldest=months[0],
            newest=months[-1],
        )

        warning = build_rates_warning(guild_id)
        if warning:
            content += f"\n{warning}"

        await inter.edit_original_response(content=content)

    @inflation_rates.sub_command(
        name="set",
        description=phrases_cmd.get("cmd_inflation_rates_set_desc", "Set the CPI value for one month"),
    )
    @commands.is_owner()
    async def inflation_rates_set(
        self,
        inter: disnake.ApplicationCommandInteraction,
        year_month: str = commands.Param(description=phrases_cmd.get("param_year_month", "Month in YYYY-MM format")),
        cpi: str = commands.Param(
            description=phrases_cmd.get("param_cpi", "CPI index for that month (101.4 means +1.4%)")
        ),
    ):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            inflation_provider.set_rate(year_month, cpi)
        except Exception as e:
            await report_error(inter, phrases, e, "rates set")
            return

        await inter.edit_original_response(
            content=format_phrase(
                phrases, "rate_saved", "CPI for `{year_month}` saved as `{cpi}`.", year_month=year_month, cpi=cpi
            )
        )

        await self.notify_all_updates()


def setup(bot):
    bot.add_cog(InflationTools(bot))
