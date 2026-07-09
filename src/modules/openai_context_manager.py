import json
import logging
import os

import settings

logger = logging.getLogger(__name__)

class OpenAIContextManager:
    def __init__(
        self, 
        context_file_name="openai_llm_context.json", 
        configs_file_name="openai_guild_configs.json",
        archives_file_name="openai_archives.json"
    ):
        self.context_file_name = context_file_name
        self.configs_file_name = configs_file_name
        self.archives_file_name = archives_file_name
        
        # Default settings
        self.default_max_messages = getattr(settings, "openai_default_max_messages", 26)
        
        self.llm_context = {}  # {"guild_id": [{"role": "...", "content": "..."}]}
        self.guild_configs = {} # {"guild_id": {"max_messages": int, "model_name": str}}
        self.archives = {}     # {"guild_id": {"archive_name": [...]}}

    async def load_from_file(self):
        # Load Context
        try:
            with open(self.context_file_name, "r", encoding="utf-8") as f:
                self.llm_context = json.load(f)
            logger.info("OpenAI LLM context is loaded from file.")
        except (FileNotFoundError, json.JSONDecodeError):
            self.llm_context = {}
            
        # Load Configs
        try:
            with open(self.configs_file_name, "r", encoding="utf-8") as f:
                self.guild_configs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.guild_configs = {}
            
        # Load Archives
        try:
            with open(self.archives_file_name, "r", encoding="utf-8") as f:
                self.archives = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.archives = {}

    def _save_json(self, file_name, data):
        dir_name = os.path.dirname(file_name)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        temp_path = file_name + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, file_name)
        except Exception as e:
            logger.error("Error writing %s: %s", file_name, e)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    async def write_to_file(self):
        self._save_json(self.context_file_name, self.llm_context)
        
    async def save_configs(self):
        self._save_json(self.configs_file_name, self.guild_configs)
        
    async def save_archives(self):
        self._save_json(self.archives_file_name, self.archives)

    # --- Config Management ---
    def get_guild_config(self, guild_id: str) -> dict:
        return self.guild_configs.get(guild_id, {})
        
    def set_guild_max_messages(self, guild_id: str, max_messages: int):
        if guild_id not in self.guild_configs:
            self.guild_configs[guild_id] = {}
        self.guild_configs[guild_id]["max_messages"] = max_messages
        
    def set_guild_model(self, guild_id: str, model_name: str):
        if guild_id not in self.guild_configs:
            self.guild_configs[guild_id] = {}
        if model_name:
            self.guild_configs[guild_id]["model_name"] = model_name
        else:
            self.guild_configs[guild_id].pop("model_name", None)

    # --- Archive Management ---
    def archive_context(self, guild_id: str, archive_name: str) -> bool:
        if guild_id not in self.llm_context or not self.llm_context[guild_id]:
            return False
        
        if guild_id not in self.archives:
            self.archives[guild_id] = {}
            
        # Copy current context to archive
        self.archives[guild_id][archive_name] = [
            {"role": m["role"], "content": m["content"]} for m in self.llm_context[guild_id]
        ]
        return True
        
    def restore_context(self, guild_id: str, archive_name: str) -> bool:
        if guild_id not in self.archives or archive_name not in self.archives[guild_id]:
            return False
            
        self.llm_context[guild_id] = [
            {"role": m["role"], "content": m["content"]} for m in self.archives[guild_id][archive_name]
        ]
        return True

    # --- Context Management ---
    def pop_last_message(self, guild_id: str) -> bool:
        if guild_id in self.llm_context and self.llm_context[guild_id]:
            # Usually pop pairs if the last is assistant
            last_msg = self.llm_context[guild_id].pop()
            if last_msg.get("role") == "assistant" and self.llm_context[guild_id] and self.llm_context[guild_id][-1].get("role") == "user":
                self.llm_context[guild_id].pop()
            return True
        return False
        
    def clear_context(self, guild_id: str):
        self.llm_context[guild_id] = []

    def get_context(self, guild_id: str) -> list:
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        
        # Return a copy to avoid mutating the internal list directly
        return [{"role": m["role"], "content": m["content"]} for m in self.llm_context[guild_id]]

    def add_user_message(self, guild_id: str, formatted_text: str, no_consent: bool = False):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        entry = {"role": "user", "content": formatted_text}
        if no_consent:
            entry["no_consent"] = True
        self.llm_context[guild_id].append(entry)

    def is_duplicate_no_consent(self, guild_id: str, author_name: str) -> bool:
        messages = self.llm_context.get(guild_id, [])
        if not messages:
            return False

        for msg in reversed(messages):
            if msg.get("role") != "user":
                return False

            if not msg.get("no_consent"):
                return False

            text = msg.get("content", "")
            if f"][{author_name}]:" in text:
                return True

        return False

    def add_model_message(self, guild_id: str, text: str):
        if guild_id not in self.llm_context:
            self.llm_context[guild_id] = []
        self.llm_context[guild_id].append({"role": "assistant", "content": text})

    def apply_restrictions(self, guild_id: str = None):
        """
        Maintains the context size within limits.
        If guild_id is provided, only applies to that guild.
        """
        guilds_to_check = [guild_id] if guild_id else self.llm_context.keys()
        
        for gid in guilds_to_check:
            if gid not in self.llm_context:
                continue
                
            messages = self.llm_context[gid]
            config = self.guild_configs.get(gid, {})
            limit = config.get("max_messages", self.default_max_messages)
            
            if len(messages) > limit:
                # Keep the last `limit` messages
                self.llm_context[gid] = messages[-limit:]
                
            # Ensure the first message is from a user
            while self.llm_context[gid] and self.llm_context[gid][0].get("role") == "assistant":
                self.llm_context[gid].pop(0)
