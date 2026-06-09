from google import genai
from google.genai import types
from pathlib import Path
import os

import core.cache

async def read_api_token():
    token_path = Path(__file__).resolve().parent.parent / ".genai_token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    return os.environ.get("GENAI_API_KEY")

async def get_new_client():
    token = await read_api_token()
    return genai.Client(api_key=token)

async def get_response(client, contents):
    return await client.aio.models.generate_content(
        model="gemma-4-31b-it",
        config=types.GenerateContentConfig(
            system_instruction=core.cache.phrases.get("olive", {}).get("system_instruction", "You're the AI assistant on the Discord server.")),
        contents=contents
    )