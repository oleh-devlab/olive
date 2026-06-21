from google import genai
from google.genai import types
from pathlib import Path
from dataclasses import dataclass, field
import time
import os
import logging

from core.utils import get_phrases

import settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when all models have exhausted their rate limits."""
    pass


@dataclass
class ModelConfig:
    """Configuration and rate-limit state for a single model."""
    name: str
    rpm: int = 15 # requests per minute
    rpd: int = 1500 # requests per day

    # Internal state
    _minute_requests: int = field(default=0, repr=False)
    _day_requests: int = field(default=0, repr=False)
    _minute_window_start: float | None = field(default=None, repr=False)
    _day_window_start: float | None = field(default=None, repr=False)

    def _reset_windows_if_needed(self, now: float):
        """Reset counters if their time windows have expired."""
        if self._minute_window_start is None or (now - self._minute_window_start) >= 60.0:
            self._minute_requests = 0
            self._minute_window_start = now

        if self._day_window_start is None or (now - self._day_window_start) >= 86400.0:
            self._day_requests = 0
            self._day_window_start = now

    def is_available(self, now: float) -> bool:
        """Check if this model can handle another request right now."""
        self._reset_windows_if_needed(now)
        return self._minute_requests < self.rpm and self._day_requests < self.rpd

    def record_request(self, now: float):
        """Increment counters after a successful request or for reservation."""
        self._reset_windows_if_needed(now)
        self._minute_requests += 1
        self._day_requests += 1

    def refund_request(self):
        """Refund a request if the API call failed."""
        if self._minute_requests > 0:
            self._minute_requests -= 1
        if self._day_requests > 0:
            self._day_requests -= 1

    def get_status(self, now: float) -> dict:
        """Return a snapshot of the current limits state for diagnostics."""
        self._reset_windows_if_needed(now)
        return {
            "model": self.name,
            "minute": f"{self._minute_requests}/{self.rpm}",
            "day": f"{self._day_requests}/{self.rpd}",
            "available": self.is_available(now),
        }


class LLMClient:
    def __init__(self):
        self.client = get_new_client()
        if not self.client:
            raise ValueError("API token for Google GenAI not found")

        self.models: list[ModelConfig] = self._load_models_config()
        if not self.models:
            raise ValueError("No models configured in phrases.json")

        logger.info("LLMClient initialized with models: %s", [m.name for m in self.models])

    @staticmethod
    def _load_models_config() -> list[ModelConfig]:
        """
        Load models from phrases.json → olive → models.
        Falls back to the legacy 'model_name' key for backward compatibility.
        """
        olive_cfg = get_phrases().get("olive", {})
        models_raw = olive_cfg.get("models")

        if models_raw and isinstance(models_raw, list):
            return [
                ModelConfig(
                    name=m["name"],
                    rpm=m.get("rpm", 15),
                    rpd=m.get("rpd", 1500),
                )
                for m in models_raw
                if isinstance(m, dict) and "name" in m
            ]

        # Legacy fallback: single model_name
        legacy_name = olive_cfg.get("model_name", "gemma-4-31b-it")
        return [ModelConfig(name=legacy_name)]

    @property
    def is_available(self) -> bool:
        """Check if at least one model can serve a request right now."""
        now = time.monotonic()
        return any(model.is_available(now) for model in self.models)

    def get_available_model(self) -> ModelConfig:
        """
        Find the first available model in priority order.

        Updates internal window states for all models.
        Raises `RateLimitExceeded` if no model can serve a request.

        Returns the available `ModelConfig` if one is found.
        """
        now = time.monotonic()

        for model in self.models:
            if model.is_available(now):
                return model

        # All models exhausted
        logger.warning(
            "All models rate-limited. Status: %s",
            [m.get_status(now) for m in self.models],
        )
        raise RateLimitExceeded("All configured models have exceeded their rate limits")

    async def connection_close(self):
        return await self.client.aio.aclose()

    async def get_response(self, contents, config) -> types.Content:
        model = self.get_available_model()
        now = time.monotonic()

        model.record_request(now)

        logger.info("Using model '%s' for request", model.name)

        try:
            response = await self.client.aio.models.generate_content(
                model=model.name,
                config=config,
                contents=contents,
            )
            return response
        except Exception:
            # TODO: Обробляти конкретні помилки, наприклад, 5xx та 4xx і решту + logging
            # + якщо помилка 500 якась там з unavaible, то треба пробувати наступну модель, оскільки інші моделі можуть працювати
            model.refund_request()
            raise

    def get_limits_status(self) -> list[dict]:
        """Return limits status for all configured models."""
        now = time.monotonic()
        return [m.get_status(now) for m in self.models]


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
