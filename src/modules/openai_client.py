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
            logger.debug("Created new aiohttp session (base_url=%s, model=%s, has_key=%s)",
                         self.base_url, self.model_name, bool(self.api_key))
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
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        
        payload = {
            "model": model_to_use,
            "messages": messages
        }
        
        logger.debug(
            "Sending request to %s | model=%s | messages=%d (system=%s)",
            url, model_to_use, len(messages), bool(system_instruction)
        )
        
        try:
            async with session.post(url, json=payload) as response:
                status = response.status
                logger.debug("Response status: %s", status)
                
                if status != 200:
                    raw = await response.text()
                    logger.error(
                        "Non-200 response from provider: status=%s url=%s body=%r",
                        status, url, raw[:500]
                    )
                    response.raise_for_status()
                
                data = await response.json()
                logger.debug("Response JSON keys: %s", list(data.keys()))
                
                if "choices" in data and len(data["choices"]) > 0:
                    content = data["choices"][0]["message"]["content"]
                    logger.debug("Got response content (%d chars)", len(content or ""))
                    return content
                else:
                    logger.error("Unexpected response format from OpenAI API: %s", data)
                    return "Sorry, I received an unexpected response format from the API."
        except aiohttp.ClientConnectorError as e:
            logger.error("Connection error — can't reach provider at %s: %s", url, e)
            return "An error occurred while connecting to the AI provider."
        except aiohttp.ClientResponseError as e:
            logger.error("HTTP error from provider: status=%s url=%s message=%s", e.status, e.request_info.url, e.message)
            return "An error occurred while connecting to the AI provider."
        except Exception as e:
            logger.exception("Unexpected exception calling OpenAI API at %s", url)
            return "An error occurred while connecting to the AI provider."
