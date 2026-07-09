import aiohttp
import logging
from pathlib import Path
import os

import settings

logger = logging.getLogger(__name__)

class OpenAIClient:
    def __init__(self):
        self.api_key = self.read_api_token()
        self.base_url = getattr(settings, "openai_api_base")
        self.model_name = getattr(settings, "openai_model_name")
        
        if not self.api_key:
            logger.warning("OpenAI API token not found.")
            
        self.session = None

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            })
        return self.session
        
    def read_api_token(self):
        token_path = Path(__file__).resolve().parent.parent / settings.paths.get("openai_token_file", ".openai_token")
        if token_path.exists():
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        return os.environ.get("OPENAI_API_KEY", "")

    async def shutdown(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_response(self, context_messages: list, system_instruction: str = None, model_override: str = None) -> str:
        session = await self.get_session()
        
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
            
        messages.extend(context_messages)
        
        model_to_use = model_override if model_override else self.model_name
        
        payload = {
            "model": model_to_use,
            "messages": messages
        }
        
        try:
            async with session.post(f"{self.base_url.rstrip('/')}/chat/completions", json=payload) as response:
                response.raise_for_status()
                data = await response.json()
                
                if "choices" in data and len(data["choices"]) > 0:
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.error("Unexpected response format from OpenAI API: %s", data)
                    return "Sorry, I received an unexpected response format from the API."
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", e)
            return "An error occurred while connecting to the AI provider."
