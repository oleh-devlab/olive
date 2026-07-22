import disnake
import logging
from datetime import datetime
from enum import Enum

from core.time_utils import tz
from core.utils import get_phrases
from modules.llm_context_manager import UserMessageMetadata

logger = logging.getLogger(__name__)

days_uk = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]

_NO_CONSENT_FALLBACK = "*This message is hidden.*"


class FormattingProfile(Enum):
    """Controls how much metadata is included in the formatted message for LLM context."""

    FULL = "full"  # Full format: [day, date time][display_name][username]: "text" + reply prefix
    AGENT = "agent"  # Minimal format for agents: [day, date time]: "text" (no author, no reply)


def _get_no_consent_placeholder() -> str:
    """Returns the no-consent placeholder text from phrases."""
    return get_phrases().get("olive", {}).get("no_consent_placeholder", _NO_CONSENT_FALLBACK)


async def format_user_message(
    message: disnake.Message,
    meta: UserMessageMetadata,
    has_consent: bool = True,
    profile: FormattingProfile = FormattingProfile.FULL,
) -> str:
    """
    Formats a Discord message into a text string for the LLM context.

    The formatting depends on the profile:
    - FULL: timestamp, author info, message content, and reply metadata.
    - AGENT: only day, date time and message content (no author, no reply).

    If the user has not given consent, the message content is replaced with a placeholder
    and reply metadata is omitted.
    """
    dt_now = datetime.fromtimestamp(meta.timestamp_ms / 1000.0, tz)
    content = message.content if has_consent else _get_no_consent_placeholder()

    day_name = days_uk[dt_now.weekday()]
    time_now = f"{day_name}, {dt_now.strftime('%d.%m.%Y %H:%M:%S')}"

    if profile == FormattingProfile.AGENT:
        return f'[{time_now}]: "{content}"'

    # FULL profile
    text = f'[{time_now}][{meta.author_display_name}][{meta.author_name}]: "{content}"'

    if has_consent and message.reference and message.reference.message_id:
        reply_prefix = _resolve_reply_prefix(message)
        if reply_prefix is None:
            reply_prefix = await _fetch_reply_prefix(message)
        if reply_prefix:
            text = f"{reply_prefix} {text}"

    return text


def _resolve_reply_prefix(message: disnake.Message) -> str | None:
    """
    Attempts to build a reply prefix from the cached resolved reference.
    Returns the prefix string, None if further fetching is needed, or "" if the reference is deleted.
    """
    try:
        replied_msg = message.reference.resolved

        if isinstance(replied_msg, disnake.DeletedReferencedMessage):
            logger.debug("Referenced message %s is deleted.", message.reference.message_id)
            return ""

        if not replied_msg:
            return None  # Not in cache, needs fetching

        if isinstance(replied_msg, disnake.Message):
            return _build_reply_prefix(replied_msg)

    except Exception as e:
        logger.warning("Error resolving reply reference: %s", e)

    return ""


async def _fetch_reply_prefix(message: disnake.Message) -> str:
    """Fetches a referenced message from the API and builds a reply prefix."""
    try:
        logger.debug("Referenced message not in cache, fetching %s", message.reference.message_id)
        replied_msg = await message.channel.fetch_message(message.reference.message_id)
        if isinstance(replied_msg, disnake.Message):
            return _build_reply_prefix(replied_msg)
    except disnake.NotFound:
        pass
    except disnake.HTTPException as e:
        logger.warning("HTTP error while fetching replied message: %s", e)
    except Exception as e:
        logger.warning("Unexpected error fetching replied message: %s", e)
    return ""


def _build_reply_prefix(replied_msg: disnake.Message) -> str:
    """Builds the '[This is a reply to ...]' prefix from a resolved message."""
    logger.debug("Successfully resolved replied message from %s", replied_msg.author.name)
    reply_dt = replied_msg.created_at.astimezone(tz)
    reply_time = f"{days_uk[reply_dt.weekday()]}, {reply_dt.strftime('%d.%m.%Y %H:%M:%S')}"
    return f"[This is a reply to {replied_msg.author.name} ({reply_time})]"
