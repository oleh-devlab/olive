import sqlite3
import logging
from typing import Optional, List, Any

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: str = "olive.sqlite3"):
        self.db_path = db_path

        self.conn: Optional[sqlite3.Connection] = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        self._apply_pragmas()

        logger.info("SQLite connection established and optimized.")

    def _apply_pragmas(self):
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")

    def execute(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error during query [{query}]: {e}")
            raise e

    def executemany(self, query: str, param_list: List[tuple]) -> None:
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.executemany(query, param_list)
        except sqlite3.Error as e:
            logger.error(f"Database error during executemany [{query}]: {e}")
            raise e

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info(f"SQLite connection with {self.db_path} closed.")


db = DatabaseManager()
