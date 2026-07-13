from google import genai
from google.genai import types, errors
from pathlib import Path
import time
import os
import json
import logging
import copy
from typing import Any

from core.utils import get_phrases
from modules.llm_rate_limiter import ModelConfig, RateLimitExceeded

import settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self):
        self.client = get_new_client()
        if not self.client:
            raise ValueError("API token for Google GenAI not found")

        self.models: list[ModelConfig] = self._load_models_config()
        if not self.models:
            raise ValueError("No models configured in phrases.json")

        self.state_file = Path("llm_limits_state.json")
        self._load_state()

        logger.info("LLMClient initialized with models: %s", [m.name for m in self.models])

    def _load_state(self):
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                for model in self.models:
                    if model.name in data:
                        model.load_from_dict(data[model.name])
                logger.info("Loaded LLM rate limits state from %s", self.state_file)
            except Exception as e:
                logger.error("Failed to load LLM rate limits state: %s", e)

    @staticmethod
    def _load_models_config() -> list[ModelConfig]:
        """
        Load models from phrases.json → olive → models.
        Falls back to the legacy 'model_name' key for backward compatibility.

        We recommend making sure that the models are ordered from “best/most expensive" to "weakest/cheapest,"
        as this may affect certain features of this class, such as the reverse cycle.
        """
        olive_cfg = get_phrases().get("olive", {})
        models_raw = olive_cfg.get("models")

        if models_raw and isinstance(models_raw, list):
            return [
                ModelConfig(
                    name=m["name"],
                    rpm=m.get("rpm", 15),
                    rpd=m.get("rpd", 1500),
                    tpm=m.get("tpm", None),
                    max_context_tokens=m.get("max_context_tokens", 128000),
                    thinking_level=m.get("thinking_level", None),
                    thinking_budget=m.get("thinking_budget", None),
                )
                for m in models_raw
                if isinstance(m, dict) and "name" in m
            ]

        # Legacy fallback: single model_name
        legacy_name = olive_cfg.get("model_name", "gemma-4-31b-it")
        return [ModelConfig(name=legacy_name)]

    @staticmethod
    def _prepare_model_config(base_config, model: ModelConfig):
        """Creates a model-specific configuration, merging base settings with model-specific overrides (like thinking)."""
        current_config = base_config
        if current_config is not None:
            current_config = copy.copy(base_config)

        if model.thinking_budget == 0:
            if not current_config:
                current_config = types.GenerateContentConfig()
            current_config.thinking_config = types.ThinkingConfig(thinking_budget=0)
        elif model.thinking_budget is not None or model.thinking_level is not None:
            if not current_config:
                current_config = types.GenerateContentConfig()

            thinking_kwargs = {}
            if model.thinking_budget is not None:
                thinking_kwargs["thinking_budget"] = model.thinking_budget

            if model.thinking_level:
                thinking_kwargs["thinking_level"] = model.thinking_level

            current_config.thinking_config = types.ThinkingConfig(**thinking_kwargs, include_thoughts=True)

        return current_config

    @property
    def is_available(self) -> bool:
        """Check if at least one model can serve a request right now."""
        now = time.time()
        return any(model.is_available(now) for model in self.models)

    @property
    def min_context_tokens(self) -> int:
        """Get the minimum context token limit across all configured models."""
        return min((model.max_context_tokens for model in self.models), default=128000)

    async def shutdown(self):
        """Close the API client and save the current limits state to disk."""
        try:
            data = {model.name: model.to_dict() for model in self.models}
            self.state_file.write_text(json.dumps(data, indent=4), encoding="utf-8")
            logger.info("Saved LLM rate limits state to %s", self.state_file)
        except Exception as e:
            logger.error("Failed to save LLM rate limits state: %s", e)

        return await self.client.aio.aclose()

    async def get_response(
        self, contents, config, cheap_first: bool = False, model_priority: list[str] | None = None
    ) -> types.Content:
        now = time.time()
        attempted_errors = []

        models_to_use = []
        if model_priority:
            models_dict = {m.name: m for m in self.models}
            models_to_use = [models_dict[name] for name in model_priority if name in models_dict]
        if not models_to_use:
            models_to_use = list(reversed(self.models)) if cheap_first else self.models

        for model in models_to_use:
            if not model.is_available(now):
                continue

            model.record_request(now)
            logger.info("Using model '%s' for request", model.name)

            try:
                # Apply model-specific configuration (e.g. thinking config)
                current_config = self._prepare_model_config(config, model)

                response = await self.client.aio.models.generate_content(
                    model=model.name,
                    config=current_config,
                    contents=contents,
                )

                if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
                    usage = response.usage_metadata
                    total_tokens = getattr(usage, "total_token_count", 0)
                    prompt_tokens = getattr(usage, "prompt_token_count", 0)
                    response_tokens = getattr(usage, "candidates_token_count", 0)
                    thoughts_tokens = getattr(usage, "thoughts_token_count", 0)

                    # TPM (Tokens Per Minute) зазвичай враховує лише вхідні токени (input)
                    model.record_tokens(time.time(), prompt_tokens)

                    logger.info(
                        "Token usage for '%s': total=%s, prompt (input)=%s, response=%s, thoughts=%s",
                        model.name,
                        total_tokens,
                        prompt_tokens,
                        response_tokens,
                        thoughts_tokens,
                    )

                model.record_success()
                return response

            except errors.APIError as e:
                model.refund_request()
                code = getattr(e, "code", 0)
                message = getattr(e, "message", str(e))
                logger.error("APIError on model '%s': code=%s, message=%s", model.name, code, message)

                # 5xx - Server Errors (Internal Server Error, Service Unavailable, etc.)
                if code == 429:
                    model.handle_429()
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning(
                        "Attempting fallback to next model due to 429 (Consecutive: %d)", model._consecutive_429s
                    )
                    continue
                elif code >= 500:
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning("Attempting fallback to next model due to server error %s", code)
                    continue

                # 4xx client errors (like 400 Bad Request) mean our request is invalid
                raise

            except Exception as e:
                model.refund_request()
                logger.error("Exception on model '%s': %s", model.name, str(e))
                attempted_errors.append(f"{model.name} (Exception: {type(e).__name__})")
                logger.warning("Attempting fallback to next model due to generic exception")
                continue

        if attempted_errors:
            error_msg = f"All attempted models failed. Errors: {', '.join(attempted_errors)}"
            logger.error(error_msg)
            raise RateLimitExceeded(error_msg)
        else:
            logger.warning(
                "All models rate-limited locally. Status: %s", [m.get_status(time.time()) for m in self.models]
            )
            raise RateLimitExceeded("All configured models have exceeded their rate limits")

    def _prepare_interaction_config(self, model: ModelConfig) -> dict:
        config = {"thinking_summaries": "auto"}
        
        if model.thinking_level is not None:
            config["thinking_level"] = model.thinking_level
                
        return config

    async def get_interaction(
        self, input_data: str | Any, system_instruction: str = None, response_format: list = None, max_output_tokens: int = None, cheap_first: bool = False, model_priority: list[str] | None = None, tools: list = None
    ):
        now = time.time()
        attempted_errors = []

        models_to_use = []
        if model_priority:
            models_dict = {m.name: m for m in self.models}
            models_to_use = [models_dict[name] for name in model_priority if name in models_dict]
        if not models_to_use:
            models_to_use = list(reversed(self.models)) if cheap_first else self.models

        for model in models_to_use:
            if not model.is_available(now):
                continue

            model.record_request(now)
            logger.info("Using model '%s' for interaction request", model.name)

            try:
                generation_config = self._prepare_interaction_config(model)
                if max_output_tokens:
                    generation_config["max_output_tokens"] = max_output_tokens
                
                kwargs = {
                    "model": model.name,
                    "store": False,
                    "input": input_data,
                }
                if generation_config:
                    kwargs["generation_config"] = generation_config
                if system_instruction:
                    kwargs["system_instruction"] = system_instruction
                if response_format:
                    kwargs["response_format"] = response_format
                if tools:
                    kwargs["tools"] = tools

                response = await self.client.aio.interactions.create(**kwargs)

                if hasattr(response, "usage") and response.usage is not None:
                    usage = response.usage
                    total_tokens = getattr(usage, "total_tokens", 0)
                    prompt_tokens = getattr(usage, "total_input_tokens", 0)
                    response_tokens = getattr(usage, "total_output_tokens", 0)
                    thoughts_tokens = getattr(usage, "total_thought_tokens", 0)

                    model.record_tokens(time.time(), prompt_tokens)

                    logger.info(
                        "Token usage for '%s': total=%s, prompt (input)=%s, response=%s, thoughts=%s",
                        model.name,
                        total_tokens,
                        prompt_tokens,
                        response_tokens,
                        thoughts_tokens,
                    )

                model.record_success()
                return response

            except errors.APIError as e:
                model.refund_request()
                code = getattr(e, "code", 0)
                message = getattr(e, "message", str(e))
                logger.error("APIError on model '%s': code=%s, message=%s", model.name, code, message)

                if code == 429:
                    model.handle_429()
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning(
                        "Attempting fallback to next model due to 429 (Consecutive: %d)", model._consecutive_429s
                    )
                    continue
                elif code >= 500:
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning("Attempting fallback to next model due to server error %s", code)
                    continue

                raise

            except Exception as e:
                model.refund_request()
                logger.error("Exception on model '%s': %s", model.name, str(e))
                attempted_errors.append(f"{model.name} (Exception: {type(e).__name__})")
                logger.warning("Attempting fallback to next model due to generic exception")
                continue

        if attempted_errors:
            error_msg = f"All attempted models failed. Errors: {', '.join(attempted_errors)}"
            logger.error(error_msg)
            raise RateLimitExceeded(error_msg)
        else:
            logger.warning(
                "All models rate-limited locally. Status: %s", [m.get_status(time.time()) for m in self.models]
            )
            raise RateLimitExceeded("All configured models have exceeded their rate limits")

    def get_limits_status(self) -> list[dict]:
        """Return limits status for all configured models."""
        now = time.time()
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
    return genai.Client(
        api_key=token,
        http_options=types.HttpOptions(
            retryOptions=types.HttpRetryOptions(attempts=2)
        )
    )
