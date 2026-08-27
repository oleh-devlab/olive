import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.environ.get("OLIVE_DB_PATH", "olive.sqlite3")

        self._conn: sqlite3.Connection | None = None
        self._connect_lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        """Lazily opens the connection on first use, so importing this module (or
        anything that references the module-level `db` singleton) has no side effects."""

        if self._conn is None:
            with self._connect_lock:
                if self._conn is None:
                    self._connect()

        return self._conn

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = conn

        self._apply_pragmas()
        self._run_migrations()

        logger.info("SQLite connection established and optimized.")

    def _run_migrations(self):
        try:
            from database.migrations import MigrationRunner

            runner = MigrationRunner(self._conn)
            runner.migrate()
        except ImportError as e:
            logger.error(f"Failed to import MigrationRunner: {e}")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    def _apply_pragmas(self):
        cursor = self._conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")

    def execute(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error during query [{query}]: {e}")
            raise

    def executemany(self, query: str, param_list: list[tuple]) -> None:
        try:
            with self.conn:
                cursor = self.conn.cursor()
                cursor.executemany(query, param_list)
        except sqlite3.Error as e:
            logger.error(f"Database error during executemany [{query}]: {e}")
            raise

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info(f"SQLite connection with {self.db_path} closed.")


db = DatabaseManager()
