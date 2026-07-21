import disnake
from disnake.ext import commands, tasks

import core.cache
from core.utils import format_embed_data, get_phrases
import settings

UPDATE_SECONDS = getattr(settings, "llm_limits_update_seconds", 30)


class LLMLimitsEmbed(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_limits.start()

    def cog_unload(self):
        self.update_limits.cancel()

    @tasks.loop(seconds=UPDATE_SECONDS)
    async def update_limits(self):
        """
        Update the LLM limits embed with current consumption stats.
        """
        raw_embed_data = (
            get_phrases()
            .get("llm_limits_embed", {})
            .get(
                "embed_data", {"title": ":robot: | LLM API Limits", "description": "Current consumption of LLM models"}
            )
        )

        formatted_embed_data = format_embed_data(raw_embed_data)
        embed = disnake.Embed.from_dict(formatted_embed_data)

        footer_text = (
            get_phrases()
            .get("utils", {})
            .get("update_interval", "Updates every {seconds} seconds.")
            .format(seconds=UPDATE_SECONDS)
        )
        embed.set_footer(text=footer_text)

        # TODO: check 25 fields limit per embed
        if getattr(core.cache, "llm_pool", None):
            unique_clients_data = core.cache.llm_pool.get_unique_clients_status()
            for client_data in unique_clients_data:
                roles_str = ", ".join(client_data["roles"])
                for status in client_data["status_list"]:
                    model_name = status["model"]
                    is_available = status["available"]
                    field_name = f"- {model_name}" if is_available else f"- ~~{model_name}~~"
                    status_text = "Ready" if is_available else "Unavailable"

                    field_value = (
                        f"`Roles: {roles_str}`\n"
                        f"`Status: {status_text}`\n"
                        f"`Req/Min: {status['minute_req']}`\n"
                        f"`Req/Day: {status['day_req']}`\n"
                        f"`Tok/Min: {status['minute_tokens']}`\n"
                        f"`Tok/Day: {status['day_tokens']}`"
                    )
                    embed.add_field(name=field_name, value=field_value, inline=False)
        else:
            embed.description = "LLM Client is not initialized or disabled."

        core.cache.embeds_to_send["llm_limits"] = embed


def setup(bot):
    bot.add_cog(LLMLimitsEmbed(bot))
