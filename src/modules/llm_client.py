from google import genai
from google.genai import types
from pathlib import Path
import os

from core.utils import get_phrases

import settings

class LLMClient:
    def __init__(self):
        self.client = get_new_client()
        if not self.client:
            raise ValueError("API token for Google GenAI not found")

        self.model_name = get_phrases().get("olive", {}).get("model_name", "gemma-4-31b-it")
        
        # TODO: implement rate limiting
        self.last_time_used = None
        self.start_time_of_minute_limit = None
        self.start_time_of_day_limit = None

        self.request_minute_limit = 15 # gemma 4
        self.request_day_limit = 1500 # gemma 4
        self.token_minute_limit = None # gemma 4
    
    async def connection_close(self):
        return await self.client.aio.aclose()

    async def get_response(self, contents, config) -> types.Content:
        return await self.client.aio.models.generate_content(
            model=self.model_name,
            config=config,
            contents=contents
        )

def read_api_token():
    token_path = Path(__file__).resolve().parent.parent / settings.paths["genai_token_file"]
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    return os.environ.get("GENAI_API_KEY")

def get_new_client():
    token = read_api_token()
    if not token:
        return None
    return genai.Client(api_key=token)
