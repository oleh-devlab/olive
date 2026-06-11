from google import genai
from google.genai import types
from pathlib import Path
import os

import core.cache

class LLMClient:
    def __init__(self):
        self.client = get_new_client()
        self.model_name = core.cache.phrases.get("olive", {}).get("model_name", "gemma-4-31b-it")

        self.last_time_used = None
        self.start_time_of_minute_limit = None
        self.start_time_of_day_limit = None

        self.request_minute_limit = 15 # gemma 4
        self.request_day_limit = 1500 # gemma 4
        self.token_minute_limit = None # gemma 4
    
    async def connection_close(self):
        return self.client.aio.aclose()

    async def get_response(self, contents) -> types.Content:
        return await self.client.aio.models.generate_content(
            model=self.model_name,
            config=types.GenerateContentConfig(
                system_instruction=core.cache.phrases.get("olive", {}).get("system_instruction", "You're the AI assistant on the Discord server.")),
            contents=contents
        )

def read_api_token():
    token_path = Path(__file__).resolve().parent.parent / ".genai_token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    return os.environ.get("GENAI_API_KEY")

def get_new_client() -> genai.Client:
    token = read_api_token()
    return genai.Client(api_key=token)
