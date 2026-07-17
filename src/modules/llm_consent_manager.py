import logging

import core.database

logger = logging.getLogger(__name__)
db = core.database.db


class LLMConsentManager:
    """
    Manages user consent for LLM data processing.
    Persists consent in the SQLite `users` table (discord_id -> has_consented_llm).
    Missing users are treated as not having given consent.
    """

    def __init__(self):
        self._consents: dict[str, bool] = {}

        self.load_from_db()

    def load_from_db(self):
        try:
            rows = db.execute("SELECT discord_id, has_consented_llm FROM users")
            logger.info("LLM consent data loaded.")
        except Exception as e:
            logger.error(f"Failed to load LLM consent data: {e}")
            rows = []
        finally:
            for row in rows:
                self._consents[str(row["discord_id"])] = bool(row["has_consented_llm"])

    def _save_to_db(self):
        db.executemany(
            """
        INSERT INTO users (discord_id, has_consented_llm) 
        VALUES (?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET 
            has_consented_llm = excluded.has_consented_llm;
        """,
            [(user_id, consent) for user_id, consent in self._consents.items()],
        )

    def has_consent(self, user_id: int) -> bool:
        """Check if a user has given consent. Missing users are treated as no consent."""
        return self._consents.get(str(user_id), False)

    def set_consent(self, user_id: int, consent: bool):
        """Set consent status for a user and persist it to the database."""
        self._consents[str(user_id)] = consent
        self._save_to_db()

    def get_consented_users_count(self) -> int:
        """Get the total number of users who have consented to LLM data processing."""
        return sum(1 for v in self._consents.values() if v)
