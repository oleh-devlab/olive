import disnake
import settings
from disnake.ext import commands, tasks

import core.cache
from core.task_handler import ResilientTaskHandler
from core.utils import format_embed_data, get_phrases


class BaseEmbedCog(commands.Cog):
    """
    Base for the cogs in `cogs/embeds/`.

    Subclasses declare where their text lives and override `get_data()`; everything
    else — the update interval, the phrases lookup, the footer, error handling and
    the write into `core.cache.embeds_to_send` — is handled here.
    """

    embed_key: str = ""  # key in core.cache.embeds_to_send
    phrases_section: str = ""  # top-level section in phrases.json
    phrases_key: str = "embed_data"  # subkey inside that section
    settings_key: str = ""  # settings.py attribute holding the interval
    default_seconds: int = 30
    fallback_embed: dict = {"title": "Embed", "description": "No data available."}

    def __init__(self, bot):
        self.bot = bot
        self.update_seconds = getattr(settings, self.settings_key, self.default_seconds)

        # `tasks.Loop` is a descriptor that clones itself on first attribute access,
        # so both the interval and the handler stay bound to this instance only.
        self.embed_loop.change_interval(seconds=self.update_seconds)
        self.error_handler = ResilientTaskHandler(bot, self.embed_loop, type(self).__name__)

        if self.should_start():
            self.embed_loop.start()

    def cog_unload(self):
        self.embed_loop.cancel()

    def should_start(self) -> bool:
        """
        Override to keep the loop from starting at all, e.g. when the hardware
        the embed reports on is absent.
        """
        return True

    async def get_data(self) -> dict | None:
        """
        Return the kwargs passed to `format_embed_data()`, or `None` to skip this
        tick and leave the previously published embed untouched.
        """
        return {}

    async def decorate(self, embed: disnake.Embed) -> None:
        """Override to add fields or rewrite the description after formatting."""

    def build_embed(self, data: dict) -> disnake.Embed:
        raw_embed_data = get_phrases().get(self.phrases_section, {}).get(self.phrases_key, self.fallback_embed)
        embed = disnake.Embed.from_dict(format_embed_data(raw_embed_data, **data))

        footer_text = (
            get_phrases()
            .get("utils", {})
            .get("update_interval", "Updates every {seconds} seconds.")
            .format(seconds=self.update_seconds)
        )
        embed.set_footer(text=footer_text)

        return embed

    # The interval here is a placeholder — `__init__` overrides it per instance.
    @tasks.loop(seconds=30)
    async def embed_loop(self):
        data = await self.get_data()
        if data is None:
            return

        embed = self.build_embed(data)
        await self.decorate(embed)

        core.cache.embeds_to_send[self.embed_key] = embed

    @embed_loop.error
    async def on_embed_loop_error(self, error):
        await self.error_handler.handle_error(error)
