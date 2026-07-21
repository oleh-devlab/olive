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

        if getattr(core.cache, "llm_pool", None):
            description_lines = [embed.description] if embed.description else []
            
            unique_clients_data = core.cache.llm_pool.get_unique_clients_status()
            
            # Find max length of left column values to align everything perfectly
            max_left_len = 0
            for client_data in unique_clients_data:
                for status in client_data["status_list"]:
                    max_left_len = max(
                        max_left_len,
                        len(str(status['minute_req'])),
                        len(str(status['minute_tokens']))
                    )

            for client_data in unique_clients_data:
                roles_str = ", ".join(client_data["roles"])
                description_lines.append(f"\n## {roles_str.title()}")
                
                for status in client_data["status_list"]:
                    model_name = status["model"]
                    is_available = status["available"]
                    
                    header = f"### {model_name}" if is_available else f"### ~~{model_name}~~"
                    status_text = "Ready" if is_available else "Unavailable"
                    
                    rpm_str = str(status['minute_req']).ljust(max_left_len)
                    tpm_str = str(status['minute_tokens']).ljust(max_left_len)
                    
                    description_lines.append(header)
                    description_lines.append(f"> **Status:** {status_text}")
                    description_lines.append(f"> **RPM:** `{rpm_str}` | **RPD:** `{status['day_req']}`")
                    description_lines.append(f"> **TPM:** `{tpm_str}` | **TPD:** `{status['day_tokens']}`")
            
            final_description = "\n".join(description_lines)
            if len(final_description) > 4000:
                embed.description = "The models exceeded the Discord embed limit."
            else:
                embed.description = final_description
        else:
            embed.description = "LLM Client is not initialized or disabled."

        core.cache.embeds_to_send["llm_limits"] = embed


def setup(bot):
    bot.add_cog(LLMLimitsEmbed(bot))
