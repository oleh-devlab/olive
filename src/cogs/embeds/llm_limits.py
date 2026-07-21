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
            total_minute_req = 0
            total_day_req = 0
            total_week_req = 0
            total_minute_tokens = 0
            total_day_tokens = 0
            total_week_tokens = 0

            # Find max length of left and middle column values to align everything perfectly
            max_left_len = 0
            max_mid_len = 0
            for client_data in unique_clients_data:
                for status in client_data["status_list"]:
                    total_minute_req += int(str(status['minute_req']).split('/')[0])
                    total_day_req += int(str(status['day_req']).split('/')[0])
                    total_week_req += int(str(status['week_req']).split('/')[0])
                    total_minute_tokens += int(str(status['minute_tokens']).split('/')[0])
                    total_day_tokens += status['day_tokens']
                    total_week_tokens += status['week_tokens']

                    max_left_len = max(
                        max_left_len,
                        len(str(status['minute_req'])),
                        len(str(status['minute_tokens']))
                    )
                    max_mid_len = max(
                        max_mid_len,
                        len(str(status['day_req'])),
                        len(str(status['day_tokens']))
                    )

            general_left_len = max(len(str(total_minute_req)), len(str(total_minute_tokens)))
            general_mid_len = max(len(str(total_day_req)), len(str(total_day_tokens)))
            
            gen_rpm_str = str(total_minute_req).ljust(general_left_len)
            gen_tpm_str = str(total_minute_tokens).ljust(general_left_len)
            gen_rpd_str = str(total_day_req).ljust(general_mid_len)
            gen_tpd_str = str(total_day_tokens).ljust(general_mid_len)

            description_lines.append("\n## General")
            description_lines.append(f"> RPM: `{gen_rpm_str}` | RPD: `{gen_rpd_str}` | RPW: `{total_week_req}`")
            description_lines.append(f"> TPM: `{gen_tpm_str}` | TPD: `{gen_tpd_str}` | TPW: `{total_week_tokens}`")

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
                    
                    rpd_str = str(status['day_req']).ljust(max_mid_len)
                    tpd_str = str(status['day_tokens']).ljust(max_mid_len)
                    
                    description_lines.append(header)
                    description_lines.append(f"> Status: {status_text}")
                    description_lines.append(f"> RPM: `{rpm_str}` | RPD: `{rpd_str}` | RPW: `{status['week_req']}`")
                    description_lines.append(f"> TPM: `{tpm_str}` | TPD: `{tpd_str}` | TPW: `{status['week_tokens']}`")
            
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
