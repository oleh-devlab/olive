import disnake
from disnake.ext import commands

import core.cache
from core.embed_cog import BaseEmbedCog


def _rate_lines(req, tokens, left_len: int, mid_len: int) -> list[str]:
    """Render the RPM/RPD/RPW and TPM/TPD/TPW pair. `req` and `tokens` are (minute, day, week) triples."""
    return [
        f"> RPM: `{str(req[0]).ljust(left_len)}` | RPD: `{str(req[1]).ljust(mid_len)}` | RPW: `{req[2]}`",
        f"> TPM: `{str(tokens[0]).ljust(left_len)}` | TPD: `{str(tokens[1]).ljust(mid_len)}` | TPW: `{tokens[2]}`",
    ]


class LLMLimitsEmbed(BaseEmbedCog):
    embed_key = "llm_limits"
    phrases_section = "llm_limits_embed"
    settings_key = "llm_limits_update_seconds"
    default_seconds = 30
    fallback_embed = {"title": ":robot: | LLM API Limits", "description": "Current consumption of LLM models"}

    async def decorate(self, embed: disnake.Embed) -> None:
        """Append current consumption stats, or say so when the pool never came up."""
        if not core.cache.llm_pool:
            embed.description = "LLM Client is not initialized or disabled."
            return

        unique_clients_data = core.cache.llm_pool.get_unique_clients_status()
        description_lines = [embed.description] if embed.description else []

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
                total_minute_req += int(str(status["minute_req"]).split("/")[0])
                total_day_req += int(str(status["day_req"]).split("/")[0])
                total_week_req += int(str(status["week_req"]).split("/")[0])
                total_minute_tokens += int(str(status["minute_tokens"]).split("/")[0])
                total_day_tokens += status["day_tokens"]
                total_week_tokens += status["week_tokens"]

                max_left_len = max(max_left_len, len(str(status["minute_req"])), len(str(status["minute_tokens"])))
                max_mid_len = max(max_mid_len, len(str(status["day_req"])), len(str(status["day_tokens"])))

        general_left_len = max(len(str(total_minute_req)), len(str(total_minute_tokens)))
        general_mid_len = max(len(str(total_day_req)), len(str(total_day_tokens)))

        description_lines.append("\n## General")
        description_lines += _rate_lines(
            (total_minute_req, total_day_req, total_week_req),
            (total_minute_tokens, total_day_tokens, total_week_tokens),
            general_left_len,
            general_mid_len,
        )

        for client_data in unique_clients_data:
            roles_str = ", ".join(client_data["roles"])
            description_lines.append(f"\n## {roles_str.title()}")

            for status in client_data["status_list"]:
                model_name = status["model"]
                is_available = status["available"]

                description_lines.append(f"### {model_name}" if is_available else f"### ~~{model_name}~~")
                description_lines.append(f"> Status: {'Ready' if is_available else 'Unavailable'}")
                description_lines += _rate_lines(
                    (status["minute_req"], status["day_req"], status["week_req"]),
                    (status["minute_tokens"], status["day_tokens"], status["week_tokens"]),
                    max_left_len,
                    max_mid_len,
                )

        final_description = "\n".join(description_lines)
        embed.description = (
            "The models exceeded the Discord embed limit." if len(final_description) > 4000 else final_description
        )


def setup(bot: commands.Bot) -> None:
    bot.add_cog(LLMLimitsEmbed(bot))
