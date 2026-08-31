import json
import logging
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

import settings
from core.utils import get_phrases
from modules.llm_rate_limiter import ModelConfig, RateLimitExceeded

logger = logging.getLogger(__name__)

# What the SDK is allowed to retry on our behalf, and what it must hand straight back.
#
# The Interactions API is not served by the SDK's own tenacity path: it goes through the
# generated client bundled at `google/genai/_gaos/`, which reads these same options through
# a translation of its own. Two of its habits decide the values here.
#
# 429 and 408 are deliberately absent. Left in, the generated client answers a 429 itself:
# it sleeps for exactly what `Retry-After` names -- the header is obeyed verbatim, and
# `max_delay` does not cap it, so a server naming an hour is slept for an hour -- and only
# surfaces the error once its own attempts are spent. Our ladder (handle_429, then the next
# model) is the faster and better-informed answer, so a 429 has to arrive unslept.
_RETRY_STATUS_CODES = [500, 502, 503, 504]

# `attempts` is documented as counting the original request, and does on the tenacity path.
# The generated client reads it as a retry count instead, so this is three requests to a
# model there and two elsewhere. The ceiling is what matters; the exact figure is not.
_RETRY_ATTEMPTS = 2
_RETRY_INITIAL_DELAY = 1.0
_RETRY_MAX_DELAY = 8.0


def _status_of(error: Exception) -> int | None:
    """The HTTP status behind an SDK error, whichever name this path spells it under.

    `google.genai.errors.APIError` carries `code`. The generated client serving Interactions
    raises classes of its own, which carry `status_code` and the response they were built
    from. Reading only one of the two is how a 5xx used to arrive here as a status-less
    exception and be logged as one.
    """
    for attr in ("code", "status_code"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value

    value = getattr(getattr(error, "response", None), "status_code", None)
    return value if isinstance(value, int) else None


class LLMClient:
    def __init__(self, token: str, state_file_suffix: str = ""):
        # Milliseconds, which is what HttpOptions takes. Unset, the SDK builds its HTTP
        # client with every timeout disabled, and one stalled request waits forever --
        # no retry ceiling helps when the attempt it counts never ends.
        timeout_ms = int(getattr(settings, "llm_request_timeout", 240) * 1000)

        self.client = genai.Client(
            api_key=token,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retryOptions=types.HttpRetryOptions(
                    attempts=_RETRY_ATTEMPTS,
                    initial_delay=_RETRY_INITIAL_DELAY,
                    max_delay=_RETRY_MAX_DELAY,
                    http_status_codes=_RETRY_STATUS_CODES,
                ),
            ),
        )

        self.models: list[ModelConfig] = self._load_models_config()
        if not self.models:
            raise ValueError("No models configured in phrases.json")

        suffix = f"_{state_file_suffix}" if state_file_suffix else ""
        self.state_file = Path(f"llm_limits_state{suffix}.json")
        self._load_state()

        logger.info("LLMClient initialized (state: %s) with models: %s", self.state_file, [m.name for m in self.models])

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
                    rpw=m.get("rpw", None),
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

    def _prepare_interaction_config(self, model: ModelConfig) -> dict:
        config = {"thinking_summaries": "auto"}

        if model.thinking_level is not None:
            config["thinking_level"] = model.thinking_level

        return config

    async def get_interaction(
        self,
        input_data: str | Any,
        system_instruction: str | None = None,
        response_format: list | None = None,
        max_output_tokens: int | None = None,
        cheap_first: bool = False,
        model_priority: list[str] | None = None,
        tools: list | None = None,
        *,
        anticipated_tokens: int,
    ):
        attempted_errors = []

        models_to_use = []
        if model_priority:
            models_dict = {m.name: m for m in self.models}
            models_to_use = [models_dict[name] for name in model_priority if name in models_dict]
        if not models_to_use:
            models_to_use = list(reversed(self.models)) if cheap_first else self.models

        for model in models_to_use:
            # Per attempt, not once per call: an earlier model's request can outlast a
            # window, and the limiter cannot tell a stale timestamp from a clock going back.
            now = time.time()
            if not model.is_available(now, anticipated_tokens=anticipated_tokens):
                continue

            model.record_request(now)
            logger.info("Using model '%s' for interaction request", model.name)

            try:
                generation_config = self._prepare_interaction_config(model)
                if max_output_tokens:
                    generation_config["max_output_tokens"] = max_output_tokens

                # --- [ARCHIVED COMMENT BEGIN] ---
                # The Google GenAI SDK and API no longer return or require the "signature" field.
                # However, older saved contexts (prior to late July 2026) might still contain it.
                # We strip it here unconditionally for ALL models to prevent BadRequestError
                # during context history validation.
                # --- [ARCHIVED COMMENT END] ---
                #
                # [UPDATE 30.07.2026]:
                # 1. Gemma (and potentially other non-Gemini models) does not support "signature" fields
                #    steps, so we must strip them out if we are not using a Gemini model.
                # 2. Gemini strictly REQUIRES the "signature" field on "thought" steps if function calls
                #    were made. Stripping it will cause a 400 BadRequestError.
                is_gemini = model.name.startswith("gemini")

                model_input = []
                for step in input_data:
                    # if isinstance(step, dict) and "signature" in step:
                    #     step_copy = step.copy()
                    #     step_copy.pop("signature", None)
                    #     model_input.append(step_copy)
                    # else:
                    #     model_input.append(step)
                    # Input could be a pydantic object or dict.
                    step_dict = step.copy() if isinstance(step, dict) else step

                    if isinstance(step_dict, dict) and not is_gemini:
                        # Strip thoughts for Gemma
                        if step_dict.get("type") == "thought":
                            continue
                        # Strip signature for Gemma
                        step_dict.pop("signature", None)

                    model_input.append(step_dict)

                kwargs = {
                    "model": model.name,
                    "store": False,
                    "input": model_input,
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

            except Exception as e:
                model.refund_request(now)
                code = _status_of(e)

                # Sometimes the code is only in the string representation
                if code is None and "429" in str(e):
                    code = 429

                if code == 429:
                    message = getattr(e, "message", str(e))
                    logger.error("APIError on model '%s': code=%s, message=%s", model.name, code, message)
                    model.handle_429(time.time())
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning(
                        "Attempting fallback to next model due to 429 (penalty on: %s)", model.penalised_limit
                    )
                    continue
                elif isinstance(code, int) and code >= 500:
                    message = getattr(e, "message", str(e))
                    logger.error("APIError on model '%s': code=%s, message=%s", model.name, code, message)
                    attempted_errors.append(f"{model.name} (APIError {code})")
                    logger.warning("Attempting fallback to next model due to server error %s", code)
                    continue

                # For 400 Bad Request or any other unexpected exceptions, we log and fallback to the next model.
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


class LLMClientPool:
    """
    Registry that deduplicates LLMClient instances by actual token value.

    If two roles (e.g. 'default' and 'schedule_agent') resolve to the same API key,
    they will share a single LLMClient and thus share rate limits.
    We assume that the request limit applies to the token.
    """

    def __init__(self):
        self._clients_by_token: dict[str, LLMClient] = {}  # token_value -> LLMClient
        self._role_to_token: dict[str, str] = {}  # role -> token_value

    def register(self, role: str, token: str) -> LLMClient:
        """
        Register a role with its token. If a client for this token already exists,
        reuse it. Otherwise, create a new one.
        """
        self._role_to_token[role] = token

        if token in self._clients_by_token:
            logger.info("Role '%s' shares LLMClient with an existing role (same token)", role)
            return self._clients_by_token[token]

        client = LLMClient(token=token, state_file_suffix=role)
        self._clients_by_token[token] = client
        logger.info("Created new LLMClient for role '%s'", role)
        return client

    def get(self, role: str = "default") -> LLMClient | None:
        """Get the LLMClient for a given role."""
        token = self._role_to_token.get(role)
        if token is None:
            return None
        return self._clients_by_token.get(token)

    @property
    def default(self) -> LLMClient | None:
        """Shortcut for the default client."""
        return self.get("default")

    @property
    def is_available(self) -> bool:
        """Check if the default client is available (backward compat)."""
        client = self.default
        return client.is_available if client else False

    async def shutdown_all(self):
        """Shutdown all unique clients."""
        for client in self._clients_by_token.values():
            await client.shutdown()

    def get_limits_by_role(self, role: str) -> list[dict] | None:
        """Return limits status for a specific role."""
        client = self.get(role)
        return client.get_limits_status() if client else None

    def get_unique_clients_status(self) -> list[dict]:
        """
        Returns status for unique clients along with the list of roles that use them.
        This is useful for displaying limits in UI (e.g. embeds) without duplicating shared limits.
        """
        token_to_roles = {}
        for role, token in self._role_to_token.items():
            token_to_roles.setdefault(token, []).append(role)

        result = []
        for token, roles in token_to_roles.items():
            client = self._clients_by_token.get(token)
            if client:
                result.append({"roles": roles, "status_list": client.get_limits_status()})
        return result
