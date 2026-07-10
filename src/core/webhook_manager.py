import logging

logger = logging.getLogger(__name__)

import json
import os
import disnake

CONFIG_PATH = "webhooks_config.json"

class WebhookManager:
    def __init__(self):
        self.config = self._load_config()

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Error decoding {CONFIG_PATH}. Using empty config.")
                return {}
            except Exception as e:
                logger.error(f"Unexpected error loading config: {e}")
                return {}
        return {}

    def _save_config(self):
        temp_path = CONFIG_PATH + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
            os.replace(temp_path, CONFIG_PATH)
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            try:
                os.remove(temp_path)
            except OSError:
                pass

    async def get_or_create_webhook(self, bot, channel: disnake.TextChannel):
        """
        Gets an existing webhook from config, or tries to find/create one in the channel.
        """
        channel_id_str = str(channel.id)
        channel_config = self.config.get(channel_id_str, {})
        
        webhook_url = channel_config.get("webhook_url")
        webhook = None
        
        # 1. Try to fetch from URL if it exists
        if webhook_url:
            try:
                session = getattr(bot.http, "_HTTPClient__session", getattr(bot.http, "_session", None))
                webhook = disnake.Webhook.from_url(webhook_url, session=session)
                # Verify it still exists on Discord
                webhook = await webhook.fetch()
            except (ValueError, disnake.NotFound):
                logger.warning(f"Saved webhook for channel {channel.id} not found on Discord. Will recreate.")
                webhook = None
            except disnake.Forbidden:
                logger.info(f"Missing access to fetch webhook for channel {channel.id}.")
                webhook = None
            except Exception as e:
                logger.error(f"Error fetching webhook by ID: {e}")
                webhook = None

        # 2. If no valid webhook from URL, try to find an existing bot webhook in the channel
        if not webhook:
            try:
                webhooks = await channel.webhooks()
                for wh in webhooks:
                    if wh.user == bot.user:
                        webhook = wh
                        break
            except disnake.Forbidden:
                logger.warning(f"Forbidden to fetch webhooks in channel {channel.id}. Need 'Manage Webhooks' permission.")
            except Exception as e:
                logger.error(f"Error fetching channel webhooks: {e}")

        # 3. If still no webhook, create one
        if not webhook:
            try:
                webhook = await channel.create_webhook(name="Olive")
                logger.info(f"Created new webhook in channel {channel.id}")
            except disnake.Forbidden:
                logger.warning(f"Forbidden to create webhook in channel {channel.id}. Need 'Manage Webhooks' permission.")
            except Exception as e:
                logger.error(f"Error creating webhook: {e}")
                
        # 4. Save the URL if we got a valid webhook
        if webhook:
            if channel_id_str not in self.config:
                self.config[channel_id_str] = {}
            
            # Only save if changed
            if self.config[channel_id_str].get("webhook_url") != webhook.url:
                self.config[channel_id_str]["webhook_url"] = webhook.url
                self._save_config()
                
        return webhook

    def get_message_id(self, channel_id: int, message_type: str):
        channel_id_str = str(channel_id)
        return self.config.get(channel_id_str, {}).get("messages", {}).get(message_type)

    def save_message_id(self, channel_id: int, message_type: str, message_id: int):
        channel_id_str = str(channel_id)
        if channel_id_str not in self.config:
            self.config[channel_id_str] = {}
            
        if "messages" not in self.config[channel_id_str]:
            self.config[channel_id_str]["messages"] = {}
            
        if self.config[channel_id_str]["messages"].get(message_type) != message_id:
            self.config[channel_id_str]["messages"][message_type] = message_id
            self._save_config()

    def get_all_tracked_message_ids(self, channel_id: int) -> list:
        channel_id_str = str(channel_id)
        messages = self.config.get(channel_id_str, {}).get("messages", {})
        return list(messages.values())

    async def purge_clean(self, channel: disnake.TextChannel):
        """
        Purges the channel, preserving all tracked eternal messages.
        """
        exclude_ids = self.get_all_tracked_message_ids(channel.id)
        try:
            await channel.purge(check=lambda m: m.id not in exclude_ids)
        except Exception as e:
            logger.error(f"Error during purge_clean in {channel.id}: {e}")

webhook_manager = WebhookManager()
