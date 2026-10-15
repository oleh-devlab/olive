"""Serves the schedule's web editor from inside the bot's own process.

Running it here rather than as a service of its own is what spares it any
syncing: the page writes through the same `ScheduleProvider`, into the same
file, on the same event loop as `/task` and the agent -- so a write from the
browser can never interleave with one from Discord, and the owner's schedule
can be redrawn the moment it lands.

Off unless `schedule_web_base_url` is set and tokens.json carries the OAuth2
application's `discord_oauth` credentials.
"""

import asyncio
import logging
import secrets

import settings
from aiohttp import web
from disnake.ext import commands

from core.token_manager import token_registry
from core.web_auth import DiscordOAuth, SessionSigner
from modules.schedule_provider import ScheduleProvider, channels_registry
from modules.schedule_web import ScheduleWebApp, oauth_redirect_uri

logger = logging.getLogger(__name__)

BIND_ATTEMPTS = 5


def has_schedule_channel(user_id: int) -> bool:
    """Who may sign in: the page edits a schedule, so only someone who has one."""
    return channels_registry.get(user_id) is not None


class ScheduleWebServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.runner: web.AppRunner | None = None
        self.cleanup_task: asyncio.Task | None = None

    async def cog_load(self):
        base_url = getattr(settings, "schedule_web_base_url", "")
        if not base_url:
            logger.info("Schedule web editor is off: schedule_web_base_url is not set.")
            return

        credentials = token_registry.get_discord_oauth()
        if credentials is None:
            logger.warning("Schedule web editor is off: tokens.json has no discord_oauth client_id/client_secret.")
            return

        secret = token_registry.get_web_session_secret()
        if not secret:
            logger.warning("tokens.json has no web_session_secret; web sessions will end when the bot restarts.")
            secret = secrets.token_urlsafe(32)

        client_id, client_secret = credentials
        session_days = getattr(settings, "schedule_web_session_days", 30)
        app = ScheduleWebApp(
            provider=ScheduleProvider(),
            oauth=DiscordOAuth(client_id, client_secret, oauth_redirect_uri(base_url)),
            signer=SessionSigner(secret, max_age_seconds=int(session_days * 24 * 3600)),
            base_url=base_url,
            is_allowed=has_schedule_channel,
            on_change=self.redraw_schedule,
        ).make_app()

        host = getattr(settings, "schedule_web_host", "127.0.0.1")
        port = getattr(settings, "schedule_web_port", 8080)

        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()

        # A few tries, because on a cog reload the old instance's server is
        # still closing (see cog_unload) when this one wants the same port.
        for attempt in range(BIND_ATTEMPTS):
            try:
                await web.TCPSite(self.runner, host, port).start()
                break
            except OSError as e:
                if attempt + 1 == BIND_ATTEMPTS:
                    logger.error("Schedule web editor could not listen on %s:%s: %s", host, port, e)
                    await self.runner.cleanup()
                    self.runner = None
                    return
                await asyncio.sleep(1)

        logger.info("Schedule web editor is listening on %s:%s for %s", host, port, base_url)

    def cog_unload(self):
        if self.runner is not None:
            self.cleanup_task = asyncio.create_task(self.runner.cleanup())
            self.runner = None

    def redraw_schedule(self, user_id: int):
        entry = channels_registry.get(user_id)
        channel_id = channels_registry.display_channel_id(entry) if entry else None
        if channel_id:
            self.bot.dispatch("schedule_update", channel_id)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(ScheduleWebServer(bot))
