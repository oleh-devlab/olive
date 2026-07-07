import sys
import json
import logging
import sqlite3
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(src_root))

project_root = src_root.parent

from core.database import db  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")


def load_json(filepath: Path) -> dict:
    if not filepath.exists():
        logger.warning(f"File {filepath} not found. Skipping.")
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {filepath}: {e}")
        return {}


def migrate():
    consent_file = Path("llm_consent.json")

    consent_data = load_json(consent_file)

    if not consent_data:
        logger.info("No data found to migrate.")
        return

    try:
        with db.conn:
            for user_id_str, consent_val in consent_data.items():
                user_id = int(user_id_str)
                consent = int(consent_val)

                db.conn.execute(
                    """
                    INSERT INTO users (discord_id, has_consented_llm)
                    VALUES (?, ?)
                    ON CONFLICT(discord_id) DO UPDATE SET 
                        has_consented_llm = excluded.has_consented_llm
                """,
                    (user_id, consent),
                )

                logger.info(f"Migrated consent for discord_id={user_id}")

        logger.info("Migration completed successfully! Data is now in SQLite.")

    except sqlite3.Error as e:
        logger.error(f"Migration failed due to database error: {e}")
    except Exception as e:
        logger.error(f"Migration failed due to unexpected error: {e}")


if __name__ == "__main__":
    migrate()
