import json
import logging

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
        with open(self.context_file_name, "w", encoding="utf-8") as f:
            json.dump(self.llm_context, f, ensure_ascii=False, indent=4)

    def get_context(self, guild_id: str) -> list:
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        return self.llm_context[guild_id]

    def add_user_message(self, guild_id: str, formatted_text: str):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        self.llm_context[guild_id].append({"role": "user", "parts": [{"text": formatted_text}]})

    def add_model_message(self, guild_id: str, text: str):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        self.llm_context[guild_id].append({"role": "model", "parts": [{"text": text}]})

    def apply_restrictions(self):
        """
        Maintains the context size within limits using a sliding window approach.
        TODO: Account for tokens
        """
        for guild_id, messages in self.llm_context.items():
            if len(messages) > self.max_messages_in_context:
                sliced_messages = messages[-self.max_messages_in_context:]
            
                # Remove leading model messages so the context always starts with a user message
                while sliced_messages and sliced_messages[0].get("role") in ["assistant", "model"]:
                    sliced_messages.pop(0)
                    
                self.llm_context[guild_id] = sliced_messages
