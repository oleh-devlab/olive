"""Shared plumbing for the "personal channel pair" pattern.

A module that gives every user two private channels — a read-only one holding an
eternal message, and one where they run the commands — needs the same registry
and the same creation dance every time. Both the schedule and the inflation cogs
are built on this.

Deliberately free of `settings` and of any module's phrases: paths, limits and
categories are passed in, so this module is unit-testable without the bot.
"""

import contextlib
import json
import logging
import os
from pathlib import Path

import disnake

from core.utils import format_phrase

logger = logging.getLogger(__name__)


class ChannelSetupError(Exception):
    """
    A channel pair could not be created.

    Carries the phrases key instead of a finished string, so the cog can render
    it for the guild it happened in.
    """

    def __init__(self, phrase_key: str, fallback: str, **format_kwargs):
        self.phrase_key = phrase_key
        self.fallback = fallback
        self.format_kwargs = format_kwargs

        super().__init__(fallback.format(**format_kwargs))

    def text(self, phrases: dict) -> str:
        return format_phrase(phrases, self.phrase_key, self.fallback, **self.format_kwargs)


class PersonalChannelRegistry:
    """
    `{owner_id: {<display key>, <management key>, "guild_id", ...}}` in one JSON file.

    The owner is usually a user, but not always: the inflation module keeps its
    public per-guild report channels here too, keyed by guild id and with no
    management channel at all.

    The key names are configurable because each module already has a file in
    production written with its own naming, and the registry has to read those
    files as they are. Unknown keys in an entry are preserved — the schedule
    keeps per-user solver settings in the same record.
    """

    def __init__(
        self,
        path: Path | str,
        display_key: str = "display_channel_id",
        management_key: str = "management_channel_id",
    ):
        self.path = Path(path)
        self.display_key = display_key
        self.management_key = management_key

    def load(self) -> dict:
        if not self.path.exists():
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Error reading {self.path}: {e}")
            return {}

    def save(self, data: dict) -> None:
        """Write the registry, replacing it atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Losing this file loses every user's channels, so the new content lands
        # in a temporary file first and replaces the old one in one step.
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            os.replace(temp_path, self.path)
        except OSError as e:
            logger.error(f"Error saving {self.path}: {e}")
            with contextlib.suppress(OSError):
                os.remove(temp_path)

    def get(self, owner_id: int) -> dict | None:
        return self.load().get(str(owner_id))

    def register(
        self,
        owner_id: int,
        guild_id: int,
        display_channel_id: int,
        management_channel_id: int | None = None,
        **extra,
    ):
        """
        Write an owner's channels, keeping any unrelated keys already in the entry.

        `management_channel_id` states what the owner has: an id sets it, `None`
        means "no management channel" and clears a stale one. Only keys this
        method knows about are touched — the schedule's solver settings live in
        the same record and have to survive.
        """
        data = self.load()
        entry = data.get(str(owner_id), {})
        entry.update({self.display_key: display_channel_id, "guild_id": guild_id, **extra})

        if management_channel_id is None:
            entry.pop(self.management_key, None)
        else:
            entry[self.management_key] = management_channel_id

        data[str(owner_id)] = entry
        self.save(data)

    def remove(self, owner_id: int) -> dict | None:
        data = self.load()
        removed = data.pop(str(owner_id), None)
        if removed is not None:
            self.save(data)

        return removed

    def count(self) -> int:
        return len(self.load())

    def count_in_guild(self, guild_id: int) -> int:
        return sum(1 for entry in self.load().values() if entry.get("guild_id") == guild_id)

    def display_channel_id(self, entry: dict) -> int | None:
        return entry.get(self.display_key)

    def iter_display_channels(self):
        """Yield `(owner_id, display_channel_id)` for every registered entry."""
        for owner_id_str, entry in self.load().items():
            channel_id = entry.get(self.display_key)
            if channel_id:
                yield int(owner_id_str), channel_id

    def iter_management_channels(self):
        """Yield `(owner_id, management_channel_id)` for every registered entry."""
        for owner_id_str, entry in self.load().items():
            channel_id = entry.get(self.management_key)
            if channel_id:
                yield int(owner_id_str), channel_id

    def find_user_by_management_channel(self, channel_id: int) -> int | None:
        """
        The user whose commands that channel carries, if any.

        Still named for users on purpose: a management channel only ever belongs
        to a personal pair, never to a guild-wide entry.
        """
        for owner_id_str, entry in self.load().items():
            if entry.get(self.management_key) == channel_id:
                return int(owner_id_str)

        return None


async def create_channel_pair(
    inter: disnake.ApplicationCommandInteraction,
    *,
    registry: PersonalChannelRegistry,
    categories: dict[int, int],
    max_per_guild: int,
    display_name: str,
    management_name: str,
    reason: str,
) -> tuple[disnake.TextChannel, disnake.TextChannel]:
    """
    Create the private channel pair for `inter.author` and register it.

    The display channel is read-only for its owner (the bot writes the eternal
    message there); the management channel accepts their commands. Both deny
    @everyone — these channels exist because the data in them is personal.

    Raises `ChannelSetupError` with a phrases key for every expected failure.
    """
    if not inter.guild or inter.guild.id not in categories:
        raise ChannelSetupError("not_available_server", "Not available on this server.")

    # Not just "is there a channel with that id": a settings entry pointing at a
    # text channel would otherwise sail through and fail as a generic creation
    # error, which tells the administrator nothing about what to fix.
    category = inter.guild.get_channel(categories[inter.guild.id])
    if not isinstance(category, disnake.CategoryChannel):
        raise ChannelSetupError(
            "category_not_found", "Category for channels not found or misconfigured. Contact administrator."
        )

    if registry.get(inter.author.id):
        raise ChannelSetupError("channel_already_exists", "You already have channels on one of the servers.")

    if registry.count_in_guild(inter.guild.id) >= max_per_guild:
        raise ChannelSetupError(
            "limit_exceeded",
            "Channel limit exceeded for this server (max {max_channels}).",
            max_channels=max_per_guild,
        )

    display_overwrites = {
        inter.guild.default_role: disnake.PermissionOverwrite(read_messages=False),
        inter.author: disnake.PermissionOverwrite(read_messages=True, send_messages=False),
        inter.guild.me: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    management_overwrites = {
        inter.guild.default_role: disnake.PermissionOverwrite(read_messages=False),
        inter.author: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
        inter.guild.me: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    display_channel = None
    try:
        display_channel = await inter.guild.create_text_channel(
            name=display_name, category=category, overwrites=display_overwrites, reason=reason
        )
        management_channel = await inter.guild.create_text_channel(
            name=management_name, category=category, overwrites=management_overwrites, reason=reason
        )
    except Exception as e:
        logger.error(f"Error creating channel pair for user {inter.author.id}: {e}")

        # Don't leave half a pair behind if only the second channel failed.
        if display_channel:
            try:
                await display_channel.delete(reason="Rollback of a failed channel pair creation")
            except Exception as cleanup_error:
                logger.warning(f"Could not roll back channel {display_channel.id}: {cleanup_error}")

        raise ChannelSetupError("creation_error", "An error occurred while creating the channels.") from e

    registry.register(inter.author.id, inter.guild.id, display_channel.id, management_channel.id)

    return display_channel, management_channel


async def create_public_channel(
    inter: disnake.ApplicationCommandInteraction,
    *,
    registry: PersonalChannelRegistry,
    categories: dict[int, int],
    owner_id: int,
    name: str,
    reason: str,
) -> disnake.TextChannel:
    """
    Create one channel everybody on the guild can read but only the bot writes to.

    The mirror image of `create_channel_pair`: same guards, same error keys style,
    but a single channel and open permissions — what lives there is the guild's
    own data, not one person's. `owner_id` is what the entry is keyed by, which
    for a guild-wide channel is the guild id itself.

    Raises `ChannelSetupError` with a phrases key for every expected failure.
    """
    if not inter.guild or inter.guild.id not in categories:
        raise ChannelSetupError("server_not_available", "Not available on this server.")

    category = inter.guild.get_channel(categories[inter.guild.id])
    if not isinstance(category, disnake.CategoryChannel):
        raise ChannelSetupError(
            "server_category_not_found", "Category for the channel not found or misconfigured. Contact administrator."
        )

    if registry.get(owner_id):
        raise ChannelSetupError("server_channel_exists", "This server already has a public report channel.")

    overwrites = {
        inter.guild.default_role: disnake.PermissionOverwrite(read_messages=True, send_messages=False),
        inter.guild.me: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    try:
        channel = await inter.guild.create_text_channel(
            name=name, category=category, overwrites=overwrites, reason=reason
        )
    except Exception as e:
        logger.error(f"Error creating public channel in guild {inter.guild.id}: {e}")
        raise ChannelSetupError("server_creation_error", "An error occurred while creating the channel.") from e

    registry.register(owner_id, inter.guild.id, channel.id)

    return channel
