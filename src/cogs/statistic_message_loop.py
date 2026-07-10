import asyncio
from datetime import datetime
from disnake.ext import commands, tasks
import disnake
import traceback

from settings import channels, embeds_blacklist
import core.cache
from core.utils import get_phrases
from core.task_handler import ResilientTaskHandler
from core.time_utils import tz
from core.webhook_manager import webhook_manager


class MessageLoop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        self.channels = []
        self.channel_webhooks = {}
        self.channel_message_ids = {}

        self.last_embeds_dicts = {}
        self.channels_valid_embeds = {}

        self.error_handler = ResilientTaskHandler(bot, self.main_loop, "Main_MessageLoop")

        self.main_loop.start()

    def cog_unload(self):
        self.main_loop.cancel()

    @tasks.loop(seconds=10)
    async def main_loop(self):
        now = datetime.now(tz)
        formatted_time = now.strftime("%d.%m.%Y %H:%M:%S")
        content = f"`{formatted_time} UTC+2`"

        # TODO: Optimize this

        for channel in self.channels:
            valid_embeds = []
            channel_id = channel.id
            channel_guild_id = channel.guild.id

            for emb_name, emb in core.cache.embeds_to_send.items():
                if (emb is not None) and (emb_name not in embeds_blacklist.get(channel_guild_id, [])):
                    valid_embeds.append(emb)

            self.channels_valid_embeds[channel_id] = valid_embeds

        # self.channels_valid_embeds = {channel_id: [emb1, emb2, ...]}

        # Compare current embeds to avoid unnecessary edits
        new_embeds_dicts = {}
        for channel_id, embeds in self.channels_valid_embeds.items():
            try:
                new_embeds_dicts[channel_id] = [e.to_dict() for e in embeds]
            except Exception:
                print(f"Error converting new embeds to dicts for {content} in channel {channel_id}.")
                new_embeds_dicts[channel_id] = []

        for channel_id, webhook in self.channel_webhooks.items():
            message_id = self.channel_message_ids.get(channel_id)
            if not message_id:
                continue
                
            if self.last_embeds_dicts.get(channel_id, []) == new_embeds_dicts.get(channel_id, []):
                # print(f"Embeds are the same, skipping edit for {content} in channel {channel_id}.")
                continue

            try:
                await webhook.edit_message(message_id, content=content, embeds=self.channels_valid_embeds[channel_id])
            except Exception as e:
                print(f"[ERROR main_loop edit] Error editing webhook message in {channel_id}: {e}")

        self.last_embeds_dicts = new_embeds_dicts

    @main_loop.before_loop
    async def before_main_loop(self):
        await self.bot.wait_until_ready()

        try:
            self.channels = []
            self.channels_valid_embeds = {}
            self.channel_webhooks = {}
            self.channel_message_ids = {}

            # 1. Getting channels and webhooks
            for channel_id in channels["statistic"]:
                try:
                    channel = await self.bot.get_or_fetch_channel(channel_id)
                    self.channels.append(channel)
                    self.channels_valid_embeds[channel.id] = []
                    
                    webhook = await webhook_manager.get_or_create_webhook(self.bot, channel)
                    if webhook:
                        self.channel_webhooks[channel.id] = webhook
                except Exception as e:
                    print(f"[before_main_loop WARNING] Not found channel {channel_id}: {e}")

            # 2. Managing messages (purge and fetch/create)
            for channel in self.channels:
                await asyncio.sleep(0.5)
                webhook = self.channel_webhooks.get(channel.id)
                if not webhook:
                    print(f"[before_main_loop WARNING] No webhook for channel {channel.id}, skipping message init.")
                    continue
                    
                message_id = webhook_manager.get_message_id(channel.id)
                msg_exists = False
                
                if message_id:
                    try:
                        # Check if message actually exists
                        await webhook.fetch_message(message_id)
                        msg_exists = True
                        self.channel_message_ids[channel.id] = message_id
                    except disnake.NotFound:
                        print(f"[before_main_loop] Saved message {message_id} not found in channel {channel.id}.")
                    except Exception as e:
                        print(f"[before_main_loop ERROR] Error fetching message {message_id}: {e}")

                try:
                    if not msg_exists:
                        await channel.purge()
                        
                        text = (
                            get_phrases(channel.guild.id)
                            .get("statistic_message_loop", {})
                            .get("welcome_message", "Error with getting message for statistic channel.")
                        )
                        
                        # Send via webhook
                        msg = await webhook.send(text, wait=True)
                        self.channel_message_ids[channel.id] = msg.id
                        webhook_manager.save_message_id(channel.id, msg.id)
                        print(f"Initial webhook message sent to channel {channel.id} for MessageLoop. Message ID: {msg.id}")
                except Exception as e:
                    print(f"[ERROR before_main_loop] Error managing channel {channel.id}: {e}")

        except Exception as e:
            print(f"[ERROR in before_main_loop]: {e}")
            traceback.print_exc()

    @main_loop.error
    async def on_main_loop_error(self, error):
        await self.error_handler.handle_error(error)


def setup(bot):
    bot.add_cog(MessageLoop(bot))
