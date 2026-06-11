import disnake
from disnake.ext import commands
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import core.cache
from core.utils import format_embed_data

tz = ZoneInfo('Europe/Kyiv')

class Errors(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users = {}
        self.last_time_not_found = datetime.now(timezone.utc) - timedelta(seconds=15)

    @commands.Cog.listener('on_command_error')
    async def on_command_error(self, ctx, error):
        await self.handle_error(ctx, error)

    @commands.Cog.listener('on_slash_command_error')
    async def on_slash_command_error(self, inter, error):
        await self.handle_error(inter, error)

    async def handle_error(self, ctx_or_inter, error):
        """Main error handler."""

        is_slash = isinstance(ctx_or_inter, disnake.ApplicationCommandInteraction)

        if isinstance(error, commands.CommandOnCooldown):
            remaining_time = error.retry_after

            local_time = datetime.now(tz)
            formatted_time = local_time.strftime('%d.%m.%Y %H:%M:%S')

            try:
                time_since_last = (datetime.now(timezone.utc) - self.users[str(ctx_or_inter.author.id)]).total_seconds()
            except KeyError:
                time_since_last = 0

            self.users[str(ctx_or_inter.author.id)] = datetime.now(timezone.utc)

            log_message = (
                f"{formatted_time} (Kyiv time):\n"
                f"User {ctx_or_inter.author.mention} in channel {ctx_or_inter.channel.mention} "
                f"encountered an error:\n> {error}"
            )
            
            message = core.cache.phrases.get("errors", {}).get("cooldown_message", "You are on cooldown. Try again in {remaining_time:.2f} seconds.").format(mention=ctx_or_inter.author.mention, remaining_time=remaining_time)
            
            if is_slash:
                await ctx_or_inter.send(message, ephemeral=True)
            else:
                await ctx_or_inter.send(message)

            # анти-флуд логіка
            if 0 < time_since_last < 4:
                raw_embed_data = core.cache.phrases.get("errors", {}).get("antiflood_kick_message_to_user_embed", {
                    "title": "Server",
                    "description": "You have been kicked for flooding commands."
                })
                embed = disnake.Embed.from_dict(raw_embed_data)
                
                try:
                    await ctx_or_inter.author.send(embed=embed)
                except disnake.Forbidden:
                    log_message += "\nSend to DM failed."

                

                log_message += f"\nLess than 4 seconds — bot tries to kick the user."
                try:
                    await ctx_or_inter.author.kick(
                        reason=(
                            f"Anti-Flood: {time_since_last:.2f} s after previous command. "
                            f"CoolDown: {remaining_time:.2f} s."
                        )
                    )
                    
                except disnake.Forbidden:
                    log_message += "\nKick failed."
                    is_kicked = False
                else:
                    is_kicked = True

                log_channel = await self.bot.get_or_fetch_channel(core.cache.channels.get("add_logs"))
                if is_kicked:
                    raw_embed_data = core.cache.phrases.get("errors", {}).get("antiflood_kicked_log_embed", {"title": "Anti-flood kicked a user"})
                    formatted_embed_data = format_embed_data(raw_embed_data, author_name=ctx_or_inter.author.name, user_mention=ctx_or_inter.author.mention, user_id=ctx_or_inter.author.id, channel_mention=ctx_or_inter.channel.mention, time_since_last=time_since_last, remaining_time=remaining_time)
                    log_embed = disnake.Embed.from_dict(formatted_embed_data)
                    
                    await log_channel.send(
                        "@everyone",
                        embed=log_embed
                    )
                else:
                    raw_embed_data = core.cache.phrases.get("errors", {}).get("antiflood_not_kicked_log_embed", {"title": "Anti-flood (almost) triggered"})
                    formatted_embed_data = format_embed_data(raw_embed_data, author_name=ctx_or_inter.author.name, user_mention=ctx_or_inter.author.mention, user_id=ctx_or_inter.author.id, channel_mention=ctx_or_inter.channel.mention, time_since_last=time_since_last, remaining_time=remaining_time)
                    log_embed = disnake.Embed.from_dict(formatted_embed_data)
                    
                    await log_channel.send(
                        embed=log_embed
                    )

            else:
                log_message += f"\nRetry after {time_since_last:.2f} seconds, which is within the normal range."

            print(f"{log_message}\n")

        elif isinstance(error, commands.CommandNotFound):
            return

            if is_slash:
                return
        
            time_refresh = (datetime.now(timezone.utc) - self.last_time_not_found).total_seconds()
            if time_refresh < 10 or len(ctx_or_inter.message.content) >= 100:
                return

            text = core.cache.phrases.get("errors", {}).get("command_not_found", "Command **\"{command}\"** not found.").format(command=ctx_or_inter.message.content.split()[0])
            await ctx_or_inter.reply(text)
            self.last_time_not_found = datetime.now(timezone.utc)

        elif isinstance(error, commands.NotOwner) or isinstance(error, commands.MissingPermissions):
            message = core.cache.phrases.get("errors", {}).get("access_denied", "You do not have the required permissions to use this command.")
            if is_slash:
                await ctx_or_inter.send(message, ephemeral=True)
            else:
                await ctx_or_inter.send(message)
            return

        else:
            raise error


def setup(bot):
    bot.add_cog(Errors(bot))
