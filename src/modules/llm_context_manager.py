import json
import logging
import os
from typing import ClassVar

from modules.llm_token_budget import LLMTokenBudget
from modules.message_metadata import UserMessageMetadata

logger = logging.getLogger(__name__)


class LLMContextManager:
    _registry: ClassVar[dict[str, "LLMContextManager"]] = {}

    def __init__(
        self,
        token_budget: LLMTokenBudget,
        context_file_name="llm_context.json",
        budget_name: str = "default",
    ):
        self.token_budget = token_budget
        self.context_file_name = context_file_name

        self.llm_context = {}  # {"discord_id": [...]} (trimmed cache)
        self.database_context = {}  # {"discord_id": [...]} (full database)
        self.context_file_size = 0  # bytes, cached at load/write time

        LLMContextManager._registry[budget_name] = self

    @classmethod
    def get_by_budget(cls, name: str) -> "LLMContextManager | None":
        return cls._registry.get(name)

    async def load_from_file(self):
        try:
            with open(self.context_file_name, "r", encoding="utf-8") as f:
                self.database_context = json.load(f)

            self.llm_context = {discord_id: list(messages) for discord_id, messages in self.database_context.items()}
            # Trim the loaded cache so we don't blow up memory/limits on startup
            self.apply_restrictions()
            self.context_file_size = os.path.getsize(self.context_file_name)
            logger.info("LLM context is loaded from file.")
        except FileNotFoundError:
            logger.warning("Context file not found. Starting with an empty context.")
            self.database_context = {}
            self.llm_context = {}
            self.context_file_size = 0
        except json.JSONDecodeError:
            logger.error("Context file is invalid. Starting with an empty context.")
            self.database_context = {}
            self.llm_context = {}
            self.context_file_size = 0
        except Exception as e:
            logger.error("Error loading LLM context from file: %s", e)
            self.database_context = {}
            self.llm_context = {}
            self.context_file_size = 0

    async def write_to_file(self):
        # TODO: fix async
        dir_name = os.path.dirname(self.context_file_name)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        temp_path = self.context_file_name + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.database_context, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, self.context_file_name)
            self.context_file_size = os.path.getsize(self.context_file_name)
        except Exception as e:
            logger.error("Error writing LLM context file: %s", e)
            try:
                os.remove(temp_path)
            except OSError as cleanup_error:
                logger.debug("Failed to remove temp file %s: %s", temp_path, cleanup_error)

    def get_interaction_context(self, discord_id: str) -> list:
        """
        Retrieves context formatted for the new Interactions API.
        The underlying JSON storage (`role` and `parts` fields) is kept in the older generateContent format
        for backward compatibility with existing saved conversation files on disk. This method dynamically
        translates them into `user_input` and `model_output` objects.
        """
        if discord_id not in self.llm_context:
            self.llm_context[discord_id] = []
        raw_steps = [self._interaction_content(m) for m in self.llm_context[discord_id]]

        # We need to interleave function_calls and function_results to satisfy Gemini's strict turn requirements.
        # It expects function_call -> function_result.
        # If we have multiple function_calls followed by multiple function_results, we must interleave them.

        # 1. Extract all function_results and index them by call_id
        results_by_id = {}
        for step in raw_steps:
            if step.get("type") == "function_result" and step.get("call_id"):
                results_by_id[step.get("call_id")] = step

        # 2. Build the interleaved list
        interleaved = []
        for step in raw_steps:
            if step.get("type") == "function_result":
                # We skip appending them here because we inject them immediately after their function_call
                continue

            interleaved.append(step)

            if step.get("type") == "function_call":
                call_id = step.get("id")
                if call_id in results_by_id:
                    interleaved.append(results_by_id[call_id])
                    # We don't delete from results_by_id in case there's some bizarre duplication,
                    # but typically it's 1:1. Actually deleting is safer so we don't duplicate if
                    # the same call_id appears twice for some reason.
                    del results_by_id[call_id]

        return interleaved

    @staticmethod
    def _interaction_content(message: dict) -> dict:
        if "interaction_step" in message:
            return message["interaction_step"]

        step_type = "user_input" if message["role"] == "user" else "model_output"
        out = {"type": step_type}
        if "parts" in message:
            content = []
            for part in message["parts"]:
                item = part.copy()
                if "text" in item and "type" not in item:
                    item["type"] = "text"
                content.append(item)
            out["content"] = content
        return out

    def add_interaction_steps(self, discord_id: str, steps: list, tokens: int = 0, timestamp_ms: int = 0):
        if discord_id not in self.llm_context:
            self.llm_context[discord_id] = []
        if discord_id not in self.database_context:
            self.database_context[discord_id] = []

        for step in steps:
            # Support both Pydantic models (from SDK) and raw dicts
            step_dict = step.model_dump() if hasattr(step, "model_dump") else step
            if not isinstance(step_dict, dict):
                step_dict = getattr(step, "__dict__", str(step))

            # TODO: If we consider the incompatibility of Gemini signatures in Gemma
            # and take additional tokens into account.
            if isinstance(step_dict, dict):
                step_dict = step_dict.copy()

                # Pydantic objects like FunctionCallStep lose their "type" field
                # when dumped. We must restore it so the API doesn't reject it on subsequent calls.
                if "type" not in step_dict and not hasattr(step, "get"):
                    class_name = step.__class__.__name__
                    if "FunctionCall" in class_name:
                        step_dict["type"] = "function_call"
                    elif "ModelOutput" in class_name or "ModelContent" in class_name or "GenerateContent" in class_name:
                        step_dict["type"] = "model_output"
                    elif "Thought" in class_name:
                        step_dict["type"] = "thought"

                # --- [ARCHIVED COMMENT BEGIN] ---
                # Skip thought blocks for compatibility (Gemma and others may not support them).
                # if step_dict.get("type") == "thought":
                #     continue
                # The "signature" field is obsolete. Discard it before saving to the DB.
                # step_dict.pop("signature", None)
                # --- [ARCHIVED COMMENT END] ---
                #
                # [UPDATE 30.07.2026]:
                # We no longer strip "thought" steps or "signature" fields at the database level.
                # Gemini strictly requires "signature" on "thought" steps to validate function calls.
                # Gemma also now generates and supports them. We will conditionally filter them
                # at runtime in LLMClient if needed, but they must be preserved in the context.

            entry = {
                "role": "model",
                "interaction_step": step_dict,
                "timestamp_ms": timestamp_ms,
            }

            # Add tokens count
            if isinstance(step_dict, dict) and step_dict.get("type") == "model_output":
                entry["tokens"] = tokens

            self.llm_context[discord_id].append(entry)
            self.database_context[discord_id].append(entry)

    def add_user_message(
        self,
        discord_id: str,
        formatted_text: str,
        meta: UserMessageMetadata,
        no_consent: bool = False,
    ):
        if discord_id not in self.llm_context:
            self.llm_context[discord_id] = []
        if discord_id not in self.database_context:
            self.database_context[discord_id] = []

        # We store "role" and "parts" for backward compatibility with older context files,
        # but these get mapped dynamically when passed to Interactions API via get_interaction_context.
        entry = {
            "role": "user",
            "parts": [{"text": formatted_text}],
            "timestamp_ms": meta.timestamp_ms,
            "author_id": meta.author_id,
            "author_name": meta.author_name,
            "author_display_name": meta.author_display_name,
            "message_id": meta.message_id,
        }
        if no_consent:
            entry["no_consent"] = True

        self.llm_context[discord_id].append(entry)
        self.database_context[discord_id].append(entry)

    def add_function_results(
        self,
        discord_id: str,
        results: list[dict],
        timestamp_ms: int = 0,
    ):
        if discord_id not in self.llm_context:
            self.llm_context[discord_id] = []
        if discord_id not in self.database_context:
            self.database_context[discord_id] = []

        for res in results:
            entry = {
                "role": "user",
                "interaction_step": res,
                "timestamp_ms": timestamp_ms,
            }
            # For backward compatibility we used to add 'parts' here, but it's redundant.
            self.llm_context[discord_id].append(entry)
            self.database_context[discord_id].append(entry)

    def is_duplicate_no_consent(self, discord_id: str, author_name: str) -> bool:
        """
        Checks whether there's already a no-consent stub from the given author in the
        current unconsented block. The block is broken by a model message or a user message
        with consent. Used to prevent consecutive stub messages from the same user.
        """
        messages = self.llm_context.get(discord_id, [])
        if not messages:
            return False

        for msg in reversed(messages):
            if msg.get("role") != "user":
                return False

            if not msg.get("no_consent"):
                return False

            if msg.get("author_name") == author_name:
                return True

            parts = msg.get("parts", [])
            if parts:
                text = parts[0].get("text", "")
                if f"][{author_name}]:" in text:
                    return True

        return False

    def get_message_tokens(self, message: dict) -> int:
        if "tokens" in message:
            return message["tokens"]
        # Fallback approximation
        return sum(len(str(p.get("text") or "")) for p in message.get("parts", [])) // 2

    def update_latest_user_message_tokens(self, discord_id: str, prompt_token_count: int):
        if discord_id not in self.llm_context or not self.llm_context[discord_id]:
            return

        messages = self.llm_context[discord_id]
        if messages[-1].get("role") == "user":
            previous_tokens = sum(self.get_message_tokens(m) for m in messages[:-1])
            new_user_tokens = prompt_token_count - previous_tokens
            if new_user_tokens <= 0:
                logger.warning(
                    "Token math yielded %d for guild %s (prompt=%d, previous_sum=%d). "
                    "This likely means fallback approximations for older messages are too high.",
                    new_user_tokens,
                    discord_id,
                    prompt_token_count,
                    previous_tokens,
                )
            messages[-1]["tokens"] = max(1, new_user_tokens)

    def get_total_tokens(self, discord_id: str) -> int:
        """Returns the total tokens for a guild's context in O(M)."""
        messages = self.llm_context.get(discord_id, [])
        return sum(self.get_message_tokens(m) for m in messages)

    @staticmethod
    def is_valid_first_message(msg: dict) -> bool:
        """
        Determines if a message is valid to be at the very start of the LLM context.
        The context must always start with a regular user message.
        """
        if msg.get("role") not in ["user"]:
            return False

        # Check if it's a function result
        step = msg.get("interaction_step", {})
        return not (isinstance(step, dict) and step.get("type") == "function_result")

    def apply_restrictions(self):
        """
        Maintains the context size within the configured token budget.
        Falls back to local approximation for legacy messages.
        """
        effective_limit = self.token_budget.context_tokens

        for messages in self.llm_context.values():
            total_tokens = sum(self.get_message_tokens(m) for m in messages)

            while messages and total_tokens > effective_limit:
                removed_msg = messages.pop(0)
                total_tokens -= self.get_message_tokens(removed_msg)

                # Remove leading messages until we find a valid first message
                while messages and not self.is_valid_first_message(messages[0]):
                    removed_msg = messages.pop(0)
                    total_tokens -= self.get_message_tokens(removed_msg)
