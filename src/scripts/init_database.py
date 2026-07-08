import sys
from pathlib import Path
import sqlite3
import logging

src_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(src_root))

import core.database  # noqa: E402

db = core.database.db

logger = logging.getLogger(__name__)

try:
    with db.conn:
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                schedule_channel_id INTEGER UNIQUE,
                management_channel_id INTEGER UNIQUE,
                planning_days INTEGER,
                priority_threshold INTEGER,
                compute_timeout REAL,
                step_minutes INTEGER
            ) STRICT
        """)

        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id INTEGER UNIQUE NOT NULL,
                has_consented_llm INTEGER NOT NULL DEFAULT 0,
                schedule_id INTEGER DEFAULT NULL,
                FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
            ) STRICT
        """)

        # db.conn.execute('''
        #     CREATE TABLE IF NOT EXISTS config (
        #         id INTEGER PRIMARY KEY AUTOINCREMENT,
        #         guild_id INTEGER UNIQUE DEFAULT NULL,

        #     ) STRICT
        # ''')

    logger.info("Database tables verified/created successfully.")
except sqlite3.Error as e:
    logger.error(f"Error initializing database tables: {e}")
