import asyncio
import datetime
import logging
import traceback

import disnake
import settings
from disnake.ext import commands

from core import cache, utils
from core.utils import format_phrase
from core.personal_channels import ChannelSetupError, create_channel_pair, create_public_channel
from modules.inflation_calculator.modules.exceptions import InflationCalculatorError, ValidationError
from modules.inflation_provider import SERVER_SCOPE, USER_SCOPE, inflation_provider
from modules.inflation_formatter import format_money
from modules.inflation_report import build_rates_warning, build_report, build_server_report, get_currency, render_page

logger = logging.getLogger(__name__)

# Read once at import time, like the schedule cog: command descriptions are
# registered with Discord on load, so `/reload_phrases` cannot change them.
phrases_cmd = utils.get_phrases().get("inflation_cmd", {})

DATE_FORMAT = "%d.%m.%Y"

# What the `scope` option offers. The values double as `inflation_provider`
# scopes, so the commands never translate between the two.
PERSONAL_CHOICE = "personal"
SCOPE_CHOICES = [PERSONAL_CHOICE, SERVER_SCOPE]


def scope_param(default: str = PERSONAL_CHOICE):
    """The `scope` option, spelled the same way in every subcommand."""
    return commands.Param(
        default=default,
        choices=SCOPE_CHOICES,
        description=phrases_cmd.get("param_scope", "Whose records to use: your own, or the server's shared budget"),
    )


def resolve_owner(inter: disnake.ApplicationCommandInteraction, scope: str, *, write: bool) -> tuple[int, str]:
    """
    Return `(owner_id, provider scope)` for a command, or raise `ChannelSetupError`.

    Reading the server budget is open to everyone — the report channel is public
    anyway — while changing it is for administrators only. `ChannelSetupError`
    is reused for the refusal because it already carries a phrases key instead of
    a finished string.
    """
    if scope != SERVER_SCOPE:
        return inter.author.id, USER_SCOPE

    if not inter.guild:
        raise ChannelSetupError("server_scope_requires_guild", "The server budget is only available on a server.")

    if write and not inter.author.guild_permissions.administrator:
        raise ChannelSetupError("server_scope_denied", "Only server administrators can change the server budget.")

    return inter.guild.id, SERVER_SCOPE


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

    def notify_update(self, owner_id: int, scope: str = USER_SCOPE):
        """Refresh the owner's eternal report message, if they have a report channel."""
        if scope == SERVER_SCOPE:
            channel_id = inflation_provider.get_server_report_channel_id(owner_id)
            event = "inflation_server_update"
        else:
            channel_id = inflation_provider.get_report_channel_id(owner_id)
            event = "inflation_update"

        if channel_id:
            self.bot.dispatch(event, channel_id)

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
        scope: str = scope_param(),
    ):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            owner_id, owner_scope = resolve_owner(inter, scope, write=True)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        try:
            date_obj = datetime.datetime.strptime(date_str, DATE_FORMAT).date()
        except ValueError:
            await inter.edit_original_response(
                content=phrases.get("invalid_date", "Invalid date format. Please use DD.MM.YYYY")
            )
            return

        try:
            record = inflation_provider.add_record(owner_id, amount, date_obj, comment, owner_scope)
        except Exception as e:
            await report_error(inter, phrases, e, "add")
            return

        self.notify_update(owner_id, owner_scope)
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
        scope: str = scope_param(),
    ):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            owner_id, owner_scope = resolve_owner(inter, scope, write=True)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        try:
            record = inflation_provider.delete_record(owner_id, record_id, owner_scope)
        except Exception as e:
            await report_error(inter, phrases, e, "delete")
            return

        self.notify_update(owner_id, owner_scope)
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
    async def report(self, inter: disnake.ApplicationCommandInteraction, scope: str = scope_param()):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)
        guild_id = inter.guild.id if inter.guild else None

        try:
            owner_id, owner_scope = resolve_owner(inter, scope, write=False)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        try:
            if owner_scope == SERVER_SCOPE:
                await inter.edit_original_response(content=build_server_report(owner_id))
                return

            summary, pages = build_report(owner_id, guild_id)
        except Exception as e:
            await report_error(inter, phrases, e, "report")
            return

        content = render_page(summary, pages, 0, guild_id)

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
    async def inflation_channel_create(self, inter: disnake.ApplicationCommandInteraction, scope: str = scope_param()):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        if scope == SERVER_SCOPE:
            await self.create_server_channel(inter, phrases)
            return

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

    async def create_server_channel(self, inter: disnake.ApplicationCommandInteraction, phrases: dict):
        """The `scope: server` half of `/inflation_channel create`."""
        try:
            resolve_owner(inter, SERVER_SCOPE, write=True)
            channel = await create_public_channel(
                inter,
                registry=inflation_provider.server_channels,
                categories=getattr(settings, "inflation_server_categories", {}),
                owner_id=inter.guild.id,
                name="inflation-server",
                reason="Automatic creation of the server inflation channel",
            )
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        self.bot.dispatch("inflation_server_init", channel, inter.guild.id)

        await inter.edit_original_response(
            content=format_phrase(
                phrases,
                "server_channel_created",
                "The public report channel is ready: {channel}",
                channel=channel.mention,
            )
        )

    async def delete_server_channel(self, inter: disnake.ApplicationCommandInteraction, phrases: dict):
        """The `scope: server` half of `/inflation_channel delete`."""
        try:
            resolve_owner(inter, SERVER_SCOPE, write=True)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        removed = inflation_provider.server_channels.remove(inter.guild.id)
        if not removed:
            await inter.edit_original_response(
                content=phrases.get("server_no_channel", "This server has no public report channel.")
            )
            return

        channel_id = removed.get("report_channel_id")
        cache.channel_states.pop(channel_id, None)

        # Records stay on disk; only the Discord channel goes away.
        await inter.edit_original_response(
            content=phrases.get(
                "server_channel_deleted", "The public report channel is being deleted. Records are kept."
            )
        )

        await self.delete_channels(channel_id, reason="Server inflation channel removed by an administrator")

    async def delete_channels(self, *channel_ids: int | None, reason: str) -> None:
        for channel_id in channel_ids:
            if not channel_id:
                continue
            try:
                channel = await self.bot.get_or_fetch_channel(channel_id)
                if channel:
                    await channel.delete(reason=reason)
            except Exception as e:
                logger.warning(f"Could not delete inflation channel {channel_id}: {e}")

    @inflation_channel.sub_command(
        name="delete",
        description=phrases_cmd.get("cmd_inflation_channel_delete_desc", "Delete your inflation channels"),
    )
    async def inflation_channel_delete(self, inter: disnake.ApplicationCommandInteraction, scope: str = scope_param()):
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        if scope == SERVER_SCOPE:
            await self.delete_server_channel(inter, phrases)
            return

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

        await self.delete_channels(
            report_channel_id,
            removed.get("management_channel_id"),
            reason="Inflation channels removed by their owner",
        )

    async def notify_all_updates(self):
        """Refresh every report message: a rate change moves everyone's numbers."""
        for _, channel_id in inflation_provider.channels.iter_display_channels():
            self.bot.dispatch("inflation_update", channel_id)
            await asyncio.sleep(0.5)

        for _, channel_id in inflation_provider.server_channels.iter_display_channels():
            self.bot.dispatch("inflation_server_update", channel_id)
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
