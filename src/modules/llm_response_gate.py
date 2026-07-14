import json
import logging

from google.genai import types

from core.utils import get_phrases
from modules.llm_rate_limiter import RateLimitExceeded

logger = logging.getLogger(__name__)

# JSON schema for the "want to reply" test response
_WANT_REPLY_SCHEMA = {
    "properties": {
        "i_want_to_reply": {
            "description": "True if you genuinely want to reply in this conversation, False if you have nothing meaningful to add.",
            "type": "boolean",
        }
    },
    "required": ["i_want_to_reply"],
    "type": "object",
}


async def want_respond(llm_client, context: list, system_instruction: str, guild_id) -> bool:
    """
    Determines whether the bot should respond in the current conversation.

    Sends a cheap test request to the LLM with the test_instruction_addition appended
    to the system instruction, expecting a JSON response with 'i_want_to_reply'.

    Returns True if:
    - No test_instruction_addition is configured (always respond)
    - The LLM decides it wants to reply

    Returns False if the LLM decides not to reply or if parsing fails.
    """
    global_olive = get_phrases().get("olive", {})
    guild_olive = get_phrases(guild_id).get("olive", {})

    test_instruction = guild_olive.get("test_instruction_addition") or global_olive.get("test_instruction_addition")

    if not test_instruction:
        return True

    test_system_instruction = f"{system_instruction}\n\n{test_instruction}"

    response_format = [
        {
            "type": "text",
            "mime_type": "application/json",
            "schema": _WANT_REPLY_SCHEMA,
        }
    ]

    test_models_priority = global_olive.get("test_models_priority")

    try:
        response = await llm_client.get_interaction(
            context,
            system_instruction=test_system_instruction,
            response_format=response_format,
            cheap_first=True,
            model_priority=test_models_priority
        )
    except RateLimitExceeded:
        logger.warning("Rate limit exceeded during response gate check, skipping response.")
        return False

    return _parse_want_reply(response)


def _parse_want_reply(response) -> bool:
    """Parses the LLM response to extract the 'i_want_to_reply' boolean."""
    try:
        if hasattr(response, "parsed") and response.parsed is not None:
            if isinstance(response.parsed, dict):
                return response.parsed.get("i_want_to_reply", False)
            return getattr(response.parsed, "i_want_to_reply", False)

        raw_text = (getattr(response, "output_text", getattr(response, "text", "")) or "").strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text[3:].strip()
            if raw_text.lower().startswith("json"):
                raw_text = raw_text[4:].strip()

        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

        logger.debug(f"Test response: \"\"\"{raw_text}\"\"\"")

        if not raw_text:
            return False

        data = json.loads(raw_text)
        return data.get("i_want_to_reply", False)

    except Exception as e:
        logger.error("Error parsing response gate JSON: %s", e)
        return False
