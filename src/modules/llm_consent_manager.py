import logging
import os

import core.database

logger = logging.getLogger(__name__)
db = core.database.db


class LLMConsentManager:
    """
    Manages user consent for LLM data processing.
    Stores consent as a JSON file mapping user IDs to their consent status.
    If a user is not in the file, they are considered as not having given consent.
    """

    def __init__(self, file_name="llm_consent.json"):
        self.file_name = file_name
        self._consents: dict[str, bool] = {}

        self.load_from_db()

    def load_from_db(self):
        try:
            rows = db.execute("SELECT discord_id, has_consented FROM users")
            logger.info("LLM consent data loaded from %s", self.file_name)
        except Exception as e:
            logger.error("Failed to load LLM consent data from %s: %s", self.file_name, e)
            rows = []
        finally:
            for row in rows:
                self._consents[str(row["discord_id"])] = bool(row["has_consented"])

    def _save_to_db(self):
        db.executemany(
            """
        INSERT INTO users (discord_id, has_consented) 
        VALUES (?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET 
            has_consented = excluded.has_consented;
        """,
            [(user_id, consent) for user_id, consent in self._consents.items()],
        )

    def has_consent(self, user_id: int) -> bool:
        """Check if a user has given consent. Missing users are treated as no consent."""
        return self._consents.get(str(user_id), False)

    def set_consent(self, user_id: int, consent: bool):
        """Set consent status for a user and persist to disk."""
        self._consents[str(user_id)] = consent
        self._save_to_db()
