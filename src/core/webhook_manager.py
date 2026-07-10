import json
import os
import disnake
import traceback
import asyncio

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
                print(f"[WebhookManager] Error decoding {CONFIG_PATH}. Using empty config.")
                return {}
            except Exception as e:
                print(f"[WebhookManager] Unexpected error loading config: {e}")
                return {}
        return {}

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"[WebhookManager] Error saving config: {e}")

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
                # Extract webhook ID from URL: https://discord.com/api/webhooks/ID/TOKEN
                parts = webhook_url.split('/')
                if len(parts) >= 6:
                    webhook_id = int(parts[-2])
                    webhook = await bot.fetch_webhook(webhook_id)
            except (ValueError, disnake.NotFound):
                print(f"[WebhookManager] Saved webhook for channel {channel.id} not found on Discord. Will recreate.")
                webhook = None
            except disnake.Forbidden:
                print(f"[WebhookManager] Missing access to fetch webhook for channel {channel.id}.")
                webhook = None
            except Exception as e:
                print(f"[WebhookManager] Error fetching webhook by ID: {e}")
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
                print(f"[WebhookManager] Forbidden to fetch webhooks in channel {channel.id}. Need 'Manage Webhooks' permission.")
            except Exception as e:
                print(f"[WebhookManager] Error fetching channel webhooks: {e}")

        # 3. If still no webhook, create one
        if not webhook:
            try:
                webhook = await channel.create_webhook(name="Olive")
                print(f"[WebhookManager] Created new webhook in channel {channel.id}")
            except disnake.Forbidden:
                print(f"[WebhookManager] Forbidden to create webhook in channel {channel.id}. Need 'Manage Webhooks' permission.")
            except Exception as e:
                print(f"[WebhookManager] Error creating webhook: {e}")
                
        # 4. Save the URL if we got a valid webhook
        if webhook:
            if channel_id_str not in self.config:
                self.config[channel_id_str] = {}
            
            # Only save if changed
            if self.config[channel_id_str].get("webhook_url") != webhook.url:
                self.config[channel_id_str]["webhook_url"] = webhook.url
                self._save_config()
                
        return webhook

    def get_message_id(self, channel_id: int):
        channel_id_str = str(channel_id)
        return self.config.get(channel_id_str, {}).get("message_id")

    def save_message_id(self, channel_id: int, message_id: int):
        channel_id_str = str(channel_id)
        if channel_id_str not in self.config:
            self.config[channel_id_str] = {}
            
        if self.config[channel_id_str].get("message_id") != message_id:
            self.config[channel_id_str]["message_id"] = message_id
            self._save_config()
            
webhook_manager = WebhookManager()
