import logging

logger = logging.getLogger(__name__)

import disnake
from core.webhook_manager import webhook_manager


class EternalMessage:
    def __init__(self, bot, channel_id: int, message_type: str):
        self.bot = bot
        self.channel_id = channel_id
        self.message_type = message_type
        self.webhook = None
        self.message_id = None
        self.guild_id = None

    async def init_message(self, default_kwargs: dict, purge_on_recreate: bool = False):
        """
        Ensures the message exists. If not, creates it using default_kwargs.
        If purge_on_recreate is True, it will purge the channel before creating a new message.
        """
        if hasattr(self.bot, "get_or_fetch_channel"):
            channel = await self.bot.get_or_fetch_channel(self.channel_id)
        else:
            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(self.channel_id)

        if not channel:
            return False

        self.guild_id = channel.guild.id if hasattr(channel, "guild") else None

        self.webhook = await webhook_manager.get_or_create_webhook(self.bot, channel)
        if not self.webhook:
            return False

        self.message_id = webhook_manager.get_message_id(self.channel_id, self.message_type)
        msg_exists = False

        if self.message_id:
            try:
                await self.webhook.fetch_message(self.message_id)
                msg_exists = True
            except disnake.NotFound:
                pass
            except Exception as e:
                logger.error(f"Error fetching {self.message_type} in {self.channel_id}: {e}")

        if not msg_exists:
            if purge_on_recreate:
                try:
                    await channel.purge()
                except Exception as e:
                    logger.error(f"Error purging channel {self.channel_id}: {e}")
            try:
                msg = await self.webhook.send(wait=True, **default_kwargs)
                self.message_id = msg.id
                webhook_manager.save_message_id(self.channel_id, self.message_type, self.message_id)
            except Exception as e:
                logger.error(f"Error creating {self.message_type} in {self.channel_id}: {e}")
                return False

        return True

    async def update(self, fallback_kwargs: dict = None, **kwargs):
        """
        Updates the message. If it was deleted, recreates it automatically.
        `fallback_kwargs` is used to recreate the message if `kwargs` only contains partial data
        (e.g., just an embed change, but we need the original text/view to recreate).
        """
        if not self.webhook or not self.message_id:
            return

        try:
            await self.webhook.edit_message(self.message_id, **kwargs)
        except disnake.NotFound:
            logger.info(f"{self.message_type} in {self.channel_id} was deleted. Recreating...")
            if fallback_kwargs is None:
                fallback_kwargs = kwargs
            try:
                msg = await self.webhook.send(wait=True, **fallback_kwargs)
                self.message_id = msg.id
                webhook_manager.save_message_id(self.channel_id, self.message_type, self.message_id)

                # If fallback_kwargs didn't contain the new updates, apply them now
                if fallback_kwargs != kwargs:
                    await self.webhook.edit_message(self.message_id, **kwargs)
            except Exception as e:
                logger.error(f"Error recreating {self.message_type} in {self.channel_id}: {e}")
        except Exception as e:
            logger.error(f"Error updating {self.message_type} in {self.channel_id}: {e}")
