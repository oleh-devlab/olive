import disnake
from disnake.ext import commands
import logging

import core.cache as cache
from core.utils import get_phrases

logger = logging.getLogger(__name__)

CONSENT_AGREE_ID = "llm_consent_agree"
CONSENT_REVOKE_ID = "llm_consent_revoke"


class ConsentView(disnake.ui.View):
    """Persistent view with Agree / Revoke buttons for LLM data consent."""

    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="✅ Agree", style=disnake.ButtonStyle.green, custom_id=CONSENT_AGREE_ID)
    async def agree_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await _handle_consent(interaction, consent=True)

    @disnake.ui.button(label="❌ Revoke", style=disnake.ButtonStyle.red, custom_id=CONSENT_REVOKE_ID)
    async def revoke_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
        await _handle_consent(interaction, consent=False)


async def _handle_consent(interaction: disnake.MessageInteraction, consent: bool):
    """Processes a consent button press: updates the manager and edits the original message."""
    if not cache.llm_consent:
        await interaction.response.send_message("Consent system is currently unavailable.", ephemeral=True)
        return

    cache.llm_consent.set_consent(interaction.author.id, consent)
    logger.info("User %s (%s) set LLM data consent to %s", interaction.author.name, interaction.author.id, consent)

    embed = _build_consent_embed(consent)
    await interaction.response.edit_message(embed=embed, view=ConsentView())


def _build_consent_embed(current_consent: bool) -> disnake.Embed:
    """Builds the consent status embed showing the explanation text and current status."""
    olive_phrases = get_phrases().get("olive", {}).get("consent", {})

    description = olive_phrases.get(
        "explanation",
        "The LLM provider used by this bot processes your messages. "
        "By agreeing, you consent to your messages being sent to the AI model for processing.",
    )

    status_label = (
        olive_phrases.get("status_agreed", "✅ Agreed")
        if current_consent
        else olive_phrases.get("status_not_agreed", "❌ Not agreed")
    )

    embed = disnake.Embed(
        title=olive_phrases.get("title", ":scroll: | OLIVE — Data Consent"),
        description=description,
        color=disnake.Color.green() if current_consent else disnake.Color.orange(),
    )
    embed.add_field(
        name=olive_phrases.get("status_field_name", "Your current status"), value=status_label, inline=False
    )
    return embed


class LLMConsentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ConsentView())

    @commands.slash_command(
        name="olive_data_consent", description="View and manage your OLIVE AI data processing consent."
    )
    async def olive_data_consent(self, ctx: disnake.ApplicationCommandInteraction):
        if not cache.llm_consent:
            await ctx.send("Consent system is currently unavailable.", ephemeral=True)
            return

        current_consent = cache.llm_consent.has_consent(ctx.author.id)
        embed = _build_consent_embed(current_consent)
        await ctx.send(embed=embed, view=ConsentView(), ephemeral=True)


def setup(bot: commands.Bot):
    bot.add_cog(LLMConsentCog(bot))
