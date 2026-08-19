"""Eternal messages that show one page at a time.

`EternalMessage` keeps a single webhook message alive; this puts a pager on top
of it. A page is a payload — text, embeds, or both — so a module that outruns
Discord's ten-embeds-per-message limit can spread them over pages instead of
dropping them.

Modules supply a `PageSource`; everything else (page clamping, the persistent
buttons, editing only when something actually changed) lives here.
"""

import logging
from dataclasses import dataclass, field

import disnake

from core import cache
from core.eternal_message import EternalMessage
from core.utils import get_phrases

logger = logging.getLogger(__name__)

# Discord's per-message limits for embeds.
MAX_EMBEDS_PER_MESSAGE = 10
MAX_EMBED_CHARS_PER_MESSAGE = 6000


@dataclass(slots=True)
class Page:
    """One screenful: message text, embeds, or both."""

    content: str | None = None
    embeds: list[disnake.Embed] = field(default_factory=list)
    # Domain data about this page — what `extra_components()` needs to build the
    # module's own buttons. Never rendered, never part of the fingerprint.
    meta: dict = field(default_factory=dict)

    def to_kwargs(self) -> dict:
        # Both keys, always. A field left out of an edit keeps its previous
        # value, so omitting `embeds` would leave the last page's embeds on
        # screen when paging onto a text-only one.
        return {"content": self.content or "", "embeds": list(self.embeds)}

    def fingerprint(self) -> tuple:
        """Comparable snapshot, used to skip pointless edits."""
        return (self.content or "", tuple(repr(embed.to_dict()) for embed in self.embeds))


def chunk_embeds(
    embeds: list[disnake.Embed],
    max_per_page: int = MAX_EMBEDS_PER_MESSAGE,
    max_chars: int = MAX_EMBED_CHARS_PER_MESSAGE,
) -> list[list[disnake.Embed]]:
    """
    Split embeds into groups that fit into a single message.

    Discord caps a message at ten embeds *and* at 6000 characters across them,
    so counting embeds is not enough. An embed that busts the character limit on
    its own gets a page to itself — nothing here can make it smaller.
    """
    pages: list[list[disnake.Embed]] = []
    current: list[disnake.Embed] = []
    current_chars = 0

    for embed in embeds:
        size = len(embed)

        if current and (len(current) >= max_per_page or current_chars + size > max_chars):
            pages.append(current)
            current = []
            current_chars = 0

        current.append(embed)
        current_chars += size

    if current:
        pages.append(current)

    return pages


class PageSource:
    """
    What a module provides to get a paginated eternal message.

    `message_type` and `view_prefix` end up in `webhooks_config.json` and in the
    buttons' `custom_id`s, so they must stay stable across releases: changing
    them orphans the message the bot is already editing.
    """

    message_type: str = ""
    view_prefix: str = ""
    # Defaults to `view_prefix`; set it when the module's phrases live under a
    # different name than its buttons.
    phrases_section: str = ""

    async def build_pages(self, user_id: int, guild_id: int | None) -> list[Page]:
        """Build every page. An empty list falls back to the welcome text."""
        raise NotImplementedError

    def welcome_text(self, guild_id: int | None) -> str:
        return "Loading..."

    def error_page(self, guild_id: int | None, error: Exception) -> Page:
        return Page(content=f"Failed to build the page: {error}")

    def header(self, guild_id: int | None) -> str:
        """Prefix prepended to every page's content, e.g. a timestamp line."""
        return ""

    def extra_components(self, page: Page, page_index: int, guild_id: int | None) -> list[disnake.ui.Item]:
        """
        Per-page components beyond the pager, built from `page.meta`.

        Their `custom_id`s can be dynamic, so they cannot be part of the
        persistent view — handle them in an `on_button_click` listener. Build
        them fresh on every call: an Item belongs to the view it was added to.
        """
        return []


class PagerButton(disnake.ui.Button):
    """Navigation button; finds its controller by the channel it was clicked in."""

    def __init__(
        self,
        prefix: str,
        action: str,
        label: str,
        style: disnake.ButtonStyle,
        disabled: bool,
        phrases_section: str,
    ):
        super().__init__(label=label, style=style, custom_id=f"{prefix}_{action}_page", disabled=disabled)

        self.action = action
        self.prefix = prefix
        self.phrases_section = phrases_section

    async def callback(self, interaction: disnake.MessageInteraction):
        controller = cache.channel_states.get(interaction.channel_id)

        # A leftover message from another module must not drive this channel's
        # controller — the pages it holds are not the ones that were clicked.
        if controller is not None and controller.source.view_prefix != self.prefix:
            logger.warning(
                "Ignoring a '%s' button in channel %s, which belongs to '%s'.",
                self.prefix,
                interaction.channel_id,
                controller.source.view_prefix,
            )
            controller = None

        if controller is None:
            guild_id = interaction.guild.id if interaction.guild else None
            phrases = get_phrases(guild_id).get(self.phrases_section, {})
            await interaction.response.send_message(
                phrases.get("state_not_found", "State not found, wait for update."), ephemeral=True
            )
            return

        await controller.handle_button(self.action, interaction)


class PaginationView(disnake.ui.View):
    """
    Persistent pager: `custom_id`s are `f"{prefix}_{action}_page"`.

    Registered once per module with `bot.add_view()` so the buttons keep working
    after a restart. Must be built inside a running event loop — disnake's View
    asks for one in `__init__`.
    """

    BUTTONS = (
        ("⏮", "first", disnake.ButtonStyle.primary),
        ("◀", "prev", disnake.ButtonStyle.primary),
        ("Refresh", "refresh", disnake.ButtonStyle.secondary),
        ("▶", "next", disnake.ButtonStyle.primary),
        ("⏭", "last", disnake.ButtonStyle.primary),
    )
    BACKWARD_ACTIONS = ("first", "prev")
    FORWARD_ACTIONS = ("next", "last")

    # Discord allows 25 components in a message; the pager itself takes five.
    MAX_COMPONENTS = 25

    @classmethod
    def for_source(
        cls,
        source: PageSource,
        prev_disabled: bool = False,
        next_disabled: bool = False,
        extra: list[disnake.ui.Item] | None = None,
    ) -> "PaginationView":
        """Build the pager a source asks for, naming and wording included."""
        return cls(
            source.view_prefix,
            prev_disabled,
            next_disabled,
            extra,
            phrases_section=source.phrases_section or source.view_prefix,
        )

    def __init__(
        self,
        prefix: str,
        prev_disabled: bool = False,
        next_disabled: bool = False,
        extra: list[disnake.ui.Item] | None = None,
        phrases_section: str = "",
    ):
        super().__init__(timeout=None)

        phrases_section = phrases_section or prefix

        for label, action, style in self.BUTTONS:
            disabled = (action in self.BACKWARD_ACTIONS and prev_disabled) or (
                action in self.FORWARD_ACTIONS and next_disabled
            )
            self.add_item(PagerButton(prefix, action, label, style, disabled, phrases_section))

        # A source that returns too many items would otherwise raise here and
        # leave the message unedited for good, so the surplus is dropped instead.
        room = self.MAX_COMPONENTS - len(self.BUTTONS)
        extra = extra or []
        if len(extra) > room:
            logger.warning("Dropping %s extra component(s): only %s fit.", len(extra) - room, room)

        for item in extra[:room]:
            self.add_item(item)


async def ensure_controller(bot, channel_id: int, user_id: int, source: PageSource) -> "PagedChannelMessage | None":
    """
    Return this channel's controller, creating and initialising one if needed.

    Init is dispatched both on startup and when the channels are created, and a
    second controller for the same channel would mean a second purge of it.
    """
    controller = cache.channel_states.get(channel_id)

    if controller is not None:
        # Re-pointing at the source keeps `/reload_cogs` meaningful: the cached
        # controller would otherwise keep calling the pre-reload module's code.
        controller.source = source
        controller.user_id = user_id
        await controller.refresh()
        return controller

    controller = PagedChannelMessage(bot, channel_id, user_id, source)

    return controller if await controller.init() else None


class PagedChannelMessage:
    """One paginated eternal message, in one channel, for one user."""

    def __init__(self, bot, channel_id: int, user_id: int, source: PageSource):
        self.bot = bot
        self.channel_id = channel_id
        self.user_id = user_id
        self.source = source
        self.em = EternalMessage(bot, channel_id, source.message_type)

        self.pages: list[Page] = []
        self.current_page = 0
        self.built = False
        self.last_state = None
        self.last_view: PaginationView | None = None

        # Building can be expensive (the schedule runs a CP-SAT solve), and the
        # loop, a command and a button can all ask at once. Overlapping requests
        # render what is already there instead of queueing another build.
        self.rebuilding = False

    @property
    def max_pages(self) -> int:
        return len(self.pages)

    def get_guild_id(self) -> int | None:
        channel = self.bot.get_channel(self.channel_id)

        return channel.guild.id if channel and channel.guild else None

    async def init(self) -> bool:
        """Create (or adopt) the eternal message and publish the first page."""
        guild_id = self.get_guild_id()
        welcome = self.source.welcome_text(guild_id)

        success = await self.em.init_message(
            {"content": welcome, "view": PaginationView.for_source(self.source)}, purge_on_recreate=True
        )
        if not success:
            logger.error(f"Failed to initialize eternal message for channel {self.channel_id}")
            return False

        cache.channel_states[self.channel_id] = self
        await self.refresh()

        return True

    async def refresh(self, rebuild: bool = True, interaction: disnake.MessageInteraction | None = None):
        """Rebuild if asked, then publish the current page if it changed."""
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            # The channel is gone; drop the controller so nothing keeps editing it.
            cache.channel_states.pop(self.channel_id, None)
            return

        guild_id = channel.guild.id if channel.guild else None

        if rebuild or not self.built:
            await self._rebuild_pages(guild_id)

        if not self.built:
            # A build is in flight and this is the first one — nothing to show yet.
            return

        self.current_page = min(max(self.current_page, 0), self.max_pages - 1)

        page = self._payload(self.current_page, guild_id)
        view = self._build_view(page, guild_id)

        state = (
            page.fingerprint(),
            tuple((item.custom_id, getattr(item, "disabled", False)) for item in view.children),
        )
        if state == self.last_state:
            return

        await self._publish(page, view, state, guild_id, interaction)

    async def _rebuild_pages(self, guild_id: int | None):
        """Ask the source for fresh pages, unless a build is already running."""
        if self.rebuilding:
            return

        self.rebuilding = True
        try:
            self.pages = await self.source.build_pages(self.user_id, guild_id)
        except Exception as e:
            logger.error(f"Error building pages for channel {self.channel_id}: {e}")
            self.pages = [self.source.error_page(guild_id, e)]
        finally:
            self.rebuilding = False

        if not self.pages:
            self.pages = [Page(content=self.source.welcome_text(guild_id))]

        self.built = True

    def _payload(self, index: int, guild_id: int | None) -> Page:
        """The page at `index`, with the source's header in front of it."""
        page = self.pages[index]

        header = self.source.header(guild_id)
        if not header:
            return page

        content = f"{header}\n\n{page.content}" if page.content else header

        # `meta` is carried over: `extra_components()` builds this page's buttons
        # out of it, and the header must not cost the page its identity.
        return Page(content=content, embeds=page.embeds, meta=page.meta)

    def _build_view(self, page: Page, guild_id: int | None) -> PaginationView:
        return PaginationView.for_source(
            self.source,
            prev_disabled=self.current_page <= 0,
            next_disabled=self.current_page >= self.max_pages - 1,
            extra=self.source.extra_components(page, self.current_page, guild_id),
        )

    async def _publish(
        self,
        page: Page,
        view: PaginationView,
        state: tuple,
        guild_id: int | None,
        interaction: disnake.MessageInteraction | None,
    ):
        kwargs = page.to_kwargs() | {"view": view}

        try:
            if interaction:
                await interaction.edit_original_response(**kwargs)
            else:
                fallback = {"content": self.source.welcome_text(guild_id), "view": view}
                await self.em.update(fallback_kwargs=fallback, **kwargs)

            self.last_state = state
            self.last_view = view
        except Exception as e:
            logger.error(f"Error editing paged message in {self.channel_id}: {e}")

    async def handle_button(self, action: str, interaction: disnake.MessageInteraction):
        try:
            await interaction.response.defer()
        except Exception as e:
            logger.debug("Failed to defer pagination interaction: %s", e)

        if action == "first":
            self.current_page = 0
        elif action == "prev":
            self.current_page -= 1
        elif action == "next":
            self.current_page += 1
        elif action == "last":
            self.current_page = max(self.max_pages - 1, 0)

        # Only the refresh button goes back to the data source.
        await self.refresh(rebuild=action == "refresh", interaction=interaction)
