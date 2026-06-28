import json
import logging
import os

logger = logging.getLogger(__name__)


class LLMConsentManager:
    """
    Manages user consent for LLM data processing.
    Stores consent as a JSON file mapping user IDs to their consent status.
    If a user is not in the file, they are considered as not having given consent.
    """

    def __init__(self, file_name="llm_consent.json"):
        self.file_name = file_name
        self._consents: dict[str, bool] = {}

    async def load_from_file(self):
        try:
            with open(self.file_name, "r", encoding="utf-8") as f:
                self._consents = json.load(f)
            logger.info("LLM consent data loaded from %s", self.file_name)
        except FileNotFoundError:
            logger.info("Consent file not found. Starting with empty consents.")
            self._consents = {}
        except json.JSONDecodeError:
            logger.error("Consent file is invalid JSON. Starting with empty consents.")
            self._consents = {}
        except Exception as e:
            logger.error("Error loading consent file: %s", e)
            self._consents = {}

    async def _save_to_file(self):
        dir_name = os.path.dirname(self.file_name)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        temp_path = self.file_name + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._consents, f, indent=2)
            os.replace(temp_path, self.file_name)
        except Exception as e:
            logger.error("Error saving consent file: %s", e)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def has_consent(self, user_id: int) -> bool:
        """Check if a user has given consent. Missing users are treated as no consent."""
        return self._consents.get(str(user_id), False)

    async def set_consent(self, user_id: int, consent: bool):
        """Set consent status for a user and persist to disk."""
        self._consents[str(user_id)] = consent
        await self._save_to_file()
