import json
import logging
import os

logger = logging.getLogger(__name__)

class LLMContextManager:
    def __init__(self, context_file_name="llm_context.json", max_messages_in_context=26):
        self.context_file_name = context_file_name
        self.max_messages_in_context = max_messages_in_context
        self.llm_context = {}  # {"guild_id": [...]}

    async def load_from_file(self):
        try:
            with open(self.context_file_name, "r", encoding="utf-8") as f:
                self.llm_context = json.load(f)
            logger.info("LLM context is loaded from file.")
        except FileNotFoundError:
            logger.warning("Context file not found. Starting with an empty context.")
            self.llm_context = {}
        except json.JSONDecodeError:
            logger.error("Context file is invalid. Starting with an empty context.")
            self.llm_context = {}
        except Exception as e:
            logger.error("Error loading LLM context from file: %s", e)
            self.llm_context = {}

    async def write_to_file(self):
        # TODO: fix async
        dir_name = os.path.dirname(self.context_file_name)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)
            
        temp_path = self.context_file_name + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.llm_context, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(temp_path, self.context_file_name)
        except Exception as e:
            logger.error("Error writing LLM context file: %s", e)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def get_context(self, guild_id: str) -> list:
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        # Return API-compatible copies — internal bookkeeping fields
        # We never mutate the stored dicts here, so token tracking keeps working.
        return [self._api_content(m) for m in self.llm_context[guild_id]]

    @staticmethod
    def _api_content(message: dict) -> dict:
        out = {"role": message["role"]}
        if "parts" in message:
            out["parts"] = message["parts"]
        return out

    def add_user_message(self, guild_id: str, formatted_text: str):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        self.llm_context[guild_id].append({"role": "user", "parts": [{"text": formatted_text}]})

    def get_message_tokens(self, message: dict) -> int:
        if "tokens" in message:
            return message["tokens"]
        # Fallback approximation
        return sum(len(str(p.get("text") or "")) for p in message.get("parts", [])) // 2

    def add_model_message(self, guild_id: str, text: str, tokens: int = 0):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        self.llm_context[guild_id].append({"role": "model", "parts": [{"text": text}], "tokens": tokens})

    def update_latest_user_message_tokens(self, guild_id: str, prompt_token_count: int):
        if guild_id not in self.llm_context or not self.llm_context[guild_id]:
            return
            
        messages = self.llm_context[guild_id]
        if messages[-1].get("role") == "user":
            previous_tokens = sum(self.get_message_tokens(m) for m in messages[:-1])
            new_user_tokens = prompt_token_count - previous_tokens
            if new_user_tokens <= 0:
                logger.warning(
                    "Token math yielded %d for guild %s (prompt=%d, previous_sum=%d). "
                    "This likely means fallback approximations for older messages are too high.",
                    new_user_tokens, guild_id, prompt_token_count, previous_tokens
                )
            messages[-1]["tokens"] = max(1, new_user_tokens)

    def apply_restrictions(self, max_tokens: int = 128000):
        """
        Maintains the context size within exact token limits.
        Falls back to local approximation for legacy messages.
        """
        # Safety margin for upcoming generated response
        effective_limit = max(0, max_tokens - 2000)

        for guild_id, messages in self.llm_context.items():
            while messages:
                total_tokens = sum(self.get_message_tokens(m) for m in messages)
                
                if total_tokens <= effective_limit:
                    break
                    
                messages.pop(0)
                
                # Remove leading model messages so the context always starts with a user message
                while messages and messages[0].get("role") in ["assistant", "model"]:
                    messages.pop(0)
