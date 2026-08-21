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
from modules.inflation_provider import (
    CAPITALIZATION_MODES,
    DEFAULT_CAPITALIZATION,
    DEFAULT_TAX_PERCENT,
    SERVER_SCOPE,
    USER_SCOPE,
    VIEW_MODES,
    inflation_provider,
)
from modules.inflation_formatter import (
    format_date,
    format_money,
    format_rate,
)
from modules.inflation_phrases import get_currency, node_name
from modules.inflation_replies import build_group_list, build_withdrawal_message
from modules.inflation_report import (
    build_rates_warning,
    build_report,
    build_server_report,
    render_page,
)

logger = logging.getLogger(__name__)

# Read once at import time, like the schedule cog: command descriptions are
# registered with Discord on load, so `/reload_phrases` cannot change them.
phrases_cmd = utils.get_phrases().get("inflation_cmd", {})

DATE_FORMAT = "%d.%m.%Y"

# What the `scope` option offers. The values double as `inflation_provider`
# scopes, so the commands never translate between the two.
PERSONAL_CHOICE = "personal"
SCOPE_CHOICES = [PERSONAL_CHOICE, SERVER_SCOPE]

# Discord shows at most 25 autocomplete suggestions.
AUTOCOMPLETE_LIMIT = 25


def scope_param(default: str = PERSONAL_CHOICE):
    """The `scope` option, spelled the same way in every subcommand."""
    return commands.Param(
        default=default,
        choices=SCOPE_CHOICES,
        description=phrases_cmd.get("param_scope", "Whose records to use: your own, or the server's shared budget"),
    )


def group_param(description_key: str, *, required: bool = False):
    """
    A group name option, autocompleted from the owner's own groups.

    Optional by default, where an empty value means "no group"; `required=True`
    omits the default entirely, which is how disnake is told the option must be
    filled in — a `default=None` would let it through as `None` instead.
    """
    kwargs = {
        "description": phrases_cmd.get(description_key, "Budget group name"),
        "autocomplete": autocomplete_group,
    }
    if not required:
        kwargs["default"] = ""

    return commands.Param(**kwargs)


def parse_date(value: str) -> datetime.date:
    """Read a DD.MM.YYYY option, raising `ChannelSetupError` on anything else.

    Reusing that error rather than `ValueError` is what lets every command route
    a bad date through the same reply path as a refused scope, phrases key and
    all, instead of repeating the message at each call site.
    """
    try:
        return datetime.datetime.strptime(value, DATE_FORMAT).date()
    except ValueError as e:
        raise ChannelSetupError("invalid_date", "Invalid date format. Please use DD.MM.YYYY") from e


async def autocomplete_group(inter: disnake.ApplicationCommandInteraction, value: str) -> list[str]:
    """
    Suggest the groups of whichever budget the `scope` option points at.

    Autocomplete fires before the command runs, so the scope has to be read off
    the half-filled interaction; anything the reader may not touch simply
    produces no suggestions rather than an error they cannot act on.
    """
    scope = inter.filled_options.get("scope", PERSONAL_CHOICE)

    try:
        owner_id, owner_scope = resolve_owner(inter, scope, write=False)
        names = [group["name"] for group in inflation_provider.list_groups(owner_id, owner_scope)]
    except Exception as e:
        logger.debug("No group suggestions for %s in scope %r: %s", inter.author.id, scope, e)
        return []

    wanted = value.strip().casefold()

    return [name for name in names if wanted in name.casefold()][:AUTOCOMPLETE_LIMIT]


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
        group: str = group_param("param_group"),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            record = inflation_provider.add_record(
                owner_id, amount, parse_date(date_str), comment, owner_scope, group=group
            )

            return format_phrase(
                phrases, "record_added", "Record added successfully! ID: {record_id}", record_id=record.get("id")
            )

        await self.run_group_action(inter, scope, "add", run)

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
        currency = get_currency(inter.guild.id if inter.guild else None)

        def run(owner_id, owner_scope, phrases):
            record = inflation_provider.delete_record(owner_id, record_id, owner_scope)

            return format_phrase(
                phrases,
                "record_deleted",
                "Record deleted successfully! (Amount: {amount})",
                amount=format_money(record.get("amount", 0), currency),
            )

        await self.run_group_action(inter, scope, "delete", run)

    @inflation.sub_command(
        name="withdraw",
        description=phrases_cmd.get("cmd_withdraw_desc", "Spend money out of a group, oldest records first"),
    )
    async def withdraw(
        self,
        inter: disnake.ApplicationCommandInteraction,
        amount: str = commands.Param(
            description=phrases_cmd.get("param_withdraw_amount", "How much to withdraw (e.g. 5000)")
        ),
        group: str = group_param("param_group"),
        scope: str = scope_param(),
    ):
        guild_id = inter.guild.id if inter.guild else None

        def run(owner_id, owner_scope, phrases):
            result = inflation_provider.withdraw(owner_id, amount, group or None, owner_scope)

            return build_withdrawal_message(result, guild_id)

        await self.run_group_action(inter, scope, "withdraw", run)

    @inflation.sub_command(
        name="report",
        description=phrases_cmd.get("cmd_report_desc", "Get your personal inflation report"),
    )
    async def report(
        self,
        inter: disnake.ApplicationCommandInteraction,
        view: str = commands.Param(
            default=None,
            choices=list(VIEW_MODES),
            description=phrases_cmd.get("param_view", "How to render this report; defaults to your saved choice"),
        ),
        scope: str = scope_param(),
    ):
        guild_id = inter.guild.id if inter.guild else None

        def run(owner_id, owner_scope, phrases):
            # The guild's report has no pager to spill onto, so it is built to
            # fit one message rather than paged and hinted at.
            if owner_scope == SERVER_SCOPE:
                return build_server_report(owner_id, mode=view)

            summary, pages, mode = build_report(owner_id, guild_id, mode=view)
            content = render_page(summary, pages, 0, guild_id, mode)

            if len(pages) > 1:
                content += "\n" + phrases.get(
                    "more_pages_hint",
                    "*Only the first page is shown here. Use `/inflation_channel create` for a paginated report.*",
                )

            return content

        await self.run_group_action(inter, scope, "report", run, write=False)

    @inflation.sub_command(
        name="view",
        description=phrases_cmd.get("cmd_view_desc", "Choose how your report channel renders records"),
    )
    async def view(
        self,
        inter: disnake.ApplicationCommandInteraction,
        mode: str = commands.Param(
            default=None,
            choices=list(VIEW_MODES),
            description=phrases_cmd.get("param_view", "Full tree of records, or group totals only"),
        ),
        collapse_interest: bool = commands.Param(
            default=None,
            description=phrases_cmd.get(
                "param_collapse_interest", "Fold a group's deposit interest into one row (default: on)"
            ),
        ),
        scope: str = scope_param(),
    ):
        # This one keeps its own body rather than going through
        # `run_group_action`: its three outcomes — reporting the current state,
        # refusing for want of a channel, and actually saving — each want a
        # different answer to "does the report need refreshing", and only the
        # last one does.
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            owner_id, owner_scope = resolve_owner(inter, scope, write=True)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        # Both options are optional because the command now sets two independent
        # things; naming neither is a request to be told the current state.
        if mode is None and collapse_interest is None:
            await inter.edit_original_response(
                content=format_phrase(
                    phrases,
                    "view_state",
                    "Report view is `{mode}`, deposit interest folded: `{collapse_interest}`.",
                    mode=inflation_provider.get_view_mode(owner_id, owner_scope),
                    collapse_interest=inflation_provider.get_collapse_interest(owner_id, owner_scope),
                )
            )
            return

        try:
            saved = True
            if mode is not None:
                saved = inflation_provider.set_view_mode(owner_id, mode, owner_scope)
            if collapse_interest is not None:
                saved = inflation_provider.set_collapse_interest(owner_id, collapse_interest, owner_scope) and saved
        except Exception as e:
            await report_error(inter, phrases, e, "view")
            return

        if not saved:
            # The preference belongs to a report channel, so there is nowhere to
            # keep it yet; a one-off rendering is still a command away.
            await inter.edit_original_response(
                content=phrases.get(
                    "view_no_channel",
                    "There is no report channel to set this for yet. Create one with `/inflation_channel create`, "
                    "or render one report with `/inflation report view:`.",
                )
            )
            return

        self.notify_update(owner_id, owner_scope)
        await inter.edit_original_response(
            content=format_phrase(
                phrases,
                "view_changed",
                "Report view set to `{mode}`, deposit interest folded: `{collapse_interest}`.",
                mode=inflation_provider.get_view_mode(owner_id, owner_scope),
                collapse_interest=inflation_provider.get_collapse_interest(owner_id, owner_scope),
            )
        )

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

    # ------------------------------------------------------------------
    # Budget groups
    # ------------------------------------------------------------------

    @commands.slash_command(
        name="inflation_groups",
        description=phrases_cmd.get("cmd_groups_desc", "Manage budget groups"),
        test_guilds=settings.guilds,
    )
    async def inflation_groups(self, inter: disnake.ApplicationCommandInteraction):
        pass

    async def run_group_action(self, inter, scope: str, action: str, run, *, write: bool = True):
        """
        The shape every group subcommand shares: resolve the owner, act, refresh.

        `run(owner_id, owner_scope, phrases)` returns the reply text; anything it raises
        becomes a user-facing message, and a successful call refreshes the
        report, because every one of these changes what the report says.

        `write=False` is for a subcommand that only reads: it takes the laxer
        permission check on the server budget and leaves the report alone.
        """
        await inter.response.defer(ephemeral=True)
        phrases = get_phrases(inter)

        try:
            owner_id, owner_scope = resolve_owner(inter, scope, write=write)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return

        try:
            content = run(owner_id, owner_scope, phrases)
        except ChannelSetupError as e:
            await inter.edit_original_response(content=e.text(phrases))
            return
        except Exception as e:
            await report_error(inter, phrases, e, action)
            return

        if write:
            self.notify_update(owner_id, owner_scope)

        await inter.edit_original_response(content=content)

    @inflation_groups.sub_command(
        name="create",
        description=phrases_cmd.get("cmd_groups_create_desc", "Create a budget group"),
    )
    async def groups_create(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(description=phrases_cmd.get("param_group_name", "Name of the new group")),
        comment: str = commands.Param(
            default="", description=phrases_cmd.get("param_group_comment", "Optional description")
        ),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            group = inflation_provider.create_group(owner_id, name, comment, owner_scope)

            return format_phrase(
                phrases,
                "group_created",
                "Group `{name}` created (ID {group_id}).",
                name=group["name"],
                group_id=group["id"],
            )

        await self.run_group_action(inter, scope, "group create", run)

    @inflation_groups.sub_command(
        name="rename",
        description=phrases_cmd.get("cmd_groups_rename_desc", "Rename a budget group"),
    )
    async def groups_rename(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        new_name: str = commands.Param(description=phrases_cmd.get("param_new_name", "The new name")),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            renamed = inflation_provider.rename_group(owner_id, group, new_name, owner_scope)

            return format_phrase(phrases, "group_renamed", "Group renamed to `{name}`.", name=renamed["name"])

        await self.run_group_action(inter, scope, "group rename", run)

    @inflation_groups.sub_command(
        name="delete",
        description=phrases_cmd.get("cmd_groups_delete_desc", "Delete a budget group"),
    )
    async def groups_delete(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        delete_records: bool = commands.Param(
            default=False,
            description=phrases_cmd.get("param_delete_records", "Delete its records too (off: they become ungrouped)"),
        ),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            deleted = inflation_provider.delete_group(owner_id, group, delete_records=delete_records, scope=owner_scope)
            key = "group_deleted_with_records" if delete_records else "group_deleted"
            default = (
                "Group `{name}` and its records are gone."
                if delete_records
                else "Group `{name}` deleted; its records are now ungrouped."
            )

            return format_phrase(phrases, key, default, name=deleted["name"])

        await self.run_group_action(inter, scope, "group delete", run)

    @inflation_groups.sub_command(
        name="assign",
        description=phrases_cmd.get("cmd_groups_assign_desc", "Move a record into a group, or out of one"),
    )
    async def groups_assign(
        self,
        inter: disnake.ApplicationCommandInteraction,
        record_id: int = commands.Param(description=phrases_cmd.get("param_record_id", "ID of the record to move")),
        group: str = group_param("param_group"),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            record = inflation_provider.assign_record(owner_id, record_id, group, owner_scope)

            # Report where it actually landed, under the group's stored name —
            # the reader may have typed it in a different case, or detached it.
            group_id = record["group_id"]
            stored = next(
                (g["name"] for g in inflation_provider.list_groups(owner_id, owner_scope) if g["id"] == group_id),
                "",
            )
            where = node_name({"id": group_id, "name": stored}, inter.guild.id if inter.guild else None)

            return format_phrase(
                phrases,
                "record_assigned",
                "Record {record_id} is now in `{group}`.",
                record_id=record_id,
                group=where,
            )

        await self.run_group_action(inter, scope, "group assign", run)

    @inflation_groups.sub_command(
        name="list",
        description=phrases_cmd.get("cmd_groups_list_desc", "List the budget groups and what is in them"),
    )
    async def groups_list(self, inter: disnake.ApplicationCommandInteraction, scope: str = scope_param()):
        guild_id = inter.guild.id if inter.guild else None

        def run(owner_id, owner_scope, phrases):
            return build_group_list(owner_id, guild_id, owner_scope)

        await self.run_group_action(inter, scope, "group list", run, write=False)

    # ------------------------------------------------------------------
    # Deposits
    # ------------------------------------------------------------------

    @commands.slash_command(
        name="inflation_deposit",
        description=phrases_cmd.get("cmd_deposit_desc", "Put a budget group under a bank deposit"),
        test_guilds=settings.guilds,
    )
    async def inflation_deposit(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @inflation_deposit.sub_command(
        name="attach",
        description=phrases_cmd.get("cmd_deposit_attach_desc", "Put a group under a deposit"),
    )
    async def deposit_attach(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        rate: str = commands.Param(
            description=phrases_cmd.get("param_rate", "Nominal annual rate the bank advertises, % (e.g. 15.5)")
        ),
        start_date: str = commands.Param(description=phrases_cmd.get("param_start_date", "Opening date, DD.MM.YYYY")),
        end_date: str = commands.Param(description=phrases_cmd.get("param_end_date", "Maturity date, DD.MM.YYYY")),
        capitalization: str = commands.Param(
            default=DEFAULT_CAPITALIZATION,
            choices=list(CAPITALIZATION_MODES),
            description=phrases_cmd.get("param_capitalization", "How often interest is added to the balance"),
        ),
        tax_percent: str = commands.Param(
            default="",
            description=phrases_cmd.get("param_tax_percent", f"Tax on interest, % (default {DEFAULT_TAX_PERCENT})"),
        ),
        early_withdrawal_rate: str = commands.Param(
            default="",
            description=phrases_cmd.get("param_early_rate", "Rate if broken early, % (default 0)"),
        ),
        tax_on_payout: bool = commands.Param(
            default=True,
            description=phrases_cmd.get("param_tax_on_payout", "Bank withholds tax at every payout (default: on)"),
        ),
        round_each_period: bool = commands.Param(
            default=True,
            description=phrases_cmd.get(
                "param_round_each_period", "Bank posts whole kopiykas each period (default: on)"
            ),
        ),
        comment: str = commands.Param(
            default="", description=phrases_cmd.get("param_deposit_comment", "Optional note about this deposit")
        ),
        scope: str = scope_param(),
    ):
        currency = get_currency(inter.guild.id if inter.guild else None)

        def run(owner_id, owner_scope, phrases):
            attached = inflation_provider.attach_deposit(
                owner_id,
                group,
                annual_rate_percent=rate,
                start_date=parse_date(start_date),
                end_date=parse_date(end_date),
                capitalization=capitalization,
                # An unset option must reach the library as None so its own
                # Ukrainian defaults apply, rather than an empty string.
                tax_percent=tax_percent or None,
                early_withdrawal_rate_percent=early_withdrawal_rate or None,
                tax_withheld_on_payout=tax_on_payout,
                round_each_period=round_each_period,
                comment=comment,
                scope=owner_scope,
            )

            projection = inflation_provider.get_deposit_projection(owner_id, group, scope=owner_scope)
            terms = inflation_provider.get_deposit_terms(owner_id, group, owner_scope)

            return format_phrase(
                phrases,
                "deposit_attached",
                "Group `{group}` is under a deposit at {rate} until {end_date}. Projected interest: {projected}.",
                group=attached["name"],
                rate=format_rate(terms.annual_rate_percent),
                end_date=format_date(terms.end_date),
                projected=format_money(projection.net_interest if projection else 0, currency),
            )

        await self.run_group_action(inter, scope, "deposit attach", run)

    @inflation_deposit.sub_command(
        name="close",
        description=phrases_cmd.get("cmd_deposit_close_desc", "Close a deposit and credit its interest"),
    )
    async def deposit_close(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        on_date: str = commands.Param(
            default="",
            description=phrases_cmd.get("param_close_date", "Close early on this date, DD.MM.YYYY (default: maturity)"),
        ),
        scope: str = scope_param(),
    ):
        currency = get_currency(inter.guild.id if inter.guild else None)

        def run(owner_id, owner_scope, phrases):
            result = inflation_provider.close_deposit(
                owner_id, group, parse_date(on_date) if on_date else None, owner_scope
            )

            # One record per capitalization period that actually paid, which is
            # why this is worth naming: a year of monthly capitalization is
            # twelve new records against the owner's limit.
            credited = sum(1 for period in result.periods if period.net_interest > 0)

            return format_phrase(
                phrases,
                "deposit_closed",
                "Deposit closed. {interest} of interest added as {count} dated record(s).",
                interest=format_money(result.net_interest, currency),
                count=credited,
                total=format_money(result.final_amount, currency),
            )

        await self.run_group_action(inter, scope, "deposit close", run)

    @inflation_deposit.sub_command(
        name="detach",
        description=phrases_cmd.get("cmd_deposit_detach_desc", "Drop a deposit without crediting interest"),
    )
    async def deposit_detach(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        scope: str = scope_param(),
    ):
        def run(owner_id, owner_scope, phrases):
            detached = inflation_provider.detach_deposit(owner_id, group, owner_scope)

            return format_phrase(
                phrases,
                "deposit_detached",
                "Deposit dropped from `{group}`. No interest was credited — use `close` for that.",
                group=detached["name"],
            )

        await self.run_group_action(inter, scope, "deposit detach", run)

    @inflation_deposit.sub_command(
        name="status",
        description=phrases_cmd.get("cmd_deposit_status_desc", "What a group's deposit has earned and will earn"),
    )
    async def deposit_status(
        self,
        inter: disnake.ApplicationCommandInteraction,
        group: str = group_param("param_group", required=True),
        scope: str = scope_param(),
    ):
        """One group's deposit, which is the question the report cannot answer.

        The report says the same numbers, but it says them about every group at
        once: past about ten groups a later one's deposit no longer fits on the
        page a command reply can show, and only the report *channel* has a pager
        to reach it. Naming a group keeps the answer one deposit long, so it
        always fits.
        """
        currency = get_currency(inter.guild.id if inter.guild else None)

        def run(owner_id, owner_scope, phrases):
            # The stored name, not what was typed: a group picked by id would
            # otherwise be reported back as a bare number.
            found = inflation_provider.find_group(owner_id, group, owner_scope)
            name = found["name"] if found else group

            terms = inflation_provider.get_deposit_terms(owner_id, group, owner_scope)
            if terms is None:
                return format_phrase(
                    phrases,
                    "deposit_status_none",
                    "`{group}` has no deposit. Attach one with `/inflation_deposit attach`.",
                    group=name,
                )

            so_far = inflation_provider.get_deposit_projection(owner_id, group, scope=owner_scope)
            projected = inflation_provider.get_deposit_projection(owner_id, group, terms.end_date, scope=owner_scope)

            return format_phrase(
                phrases,
                "deposit_status",
                "**{group}** — {rate} until {end_date} ({capitalization} capitalization)\n"
                "Earned so far: `{earned}` (balance `{balance}`)\n"
                "Projected at maturity: `{projected}` (total `{projected_total}`, effective `{effective_rate}`)",
                group=name,
                rate=format_rate(terms.annual_rate_percent),
                end_date=format_date(terms.end_date),
                capitalization=terms.capitalization.value,
                earned=format_money(so_far.net_interest, currency),
                balance=format_money(so_far.final_amount, currency),
                projected=format_money(projected.net_interest, currency),
                projected_total=format_money(projected.final_amount, currency),
                effective_rate=format_rate(projected.effective_annual_rate_percent),
            )

        await self.run_group_action(inter, scope, "deposit status", run, write=False)


def setup(bot):
    bot.add_cog(InflationTools(bot))
