import sqlite3
import logging
from pathlib import Path
import re

logger = logging.getLogger(__name__)

class MigrationRunner:
    """SQLite schema migration runner using PRAGMA user_version."""

    def __init__(self, conn: sqlite3.Connection, migrations_dir: Path = None):
        self.conn = conn
        if migrations_dir is None:
            self.migrations_dir = Path(__file__).resolve().parent
        else:
            self.migrations_dir = Path(migrations_dir)
            
        self._migrations = None
        

    def get_current_version(self) -> int:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA user_version;")
        return cursor.fetchone()[0]

    def get_latest_version(self) -> int:
        migrations = self._load_migration_files()
        if not migrations:
            return 0
        return migrations[-1][0]

    def migrate(self) -> None:
        current = self.get_current_version()
        latest = self.get_latest_version()

        if current == 0:
            # Check if this is a fresh DB or a legacy DB
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedules';")
            if cursor.fetchone():
                logger.info("Detected legacy database (has tables but user_version=0). Setting user_version=1.")
                self.conn.execute("PRAGMA user_version = 1")
                self.conn.commit()
                current = 1
            else:
                logger.info("Initializing fresh database from migrations.")

        if current < latest:
            logger.info(f"Database requires migration (current: {current}, latest: {latest}).")
            self._apply_pending(current, latest)
        else:
            logger.debug(f"Database is up to date (version {current}).")



    def _apply_pending(self, current: int, latest: int) -> None:
        migrations = self._load_migration_files()
        
        pending_scripts = []
        applied_names = []
        for version, path in migrations:
            if version > current:
                logger.info(f"Preparing migration: {path.name}")
                sql = path.read_text(encoding="utf-8")
                pending_scripts.append(f"-- Migration {version}\n{sql}\nPRAGMA user_version = {version};")
                applied_names.append(path.name)
                
        if not pending_scripts:
            return
            
        # Store original FK state
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        fk_state = cursor.fetchone()[0]
        
        # Turn off FKs *before* starting the transaction
        full_script = "PRAGMA foreign_keys = OFF;\nBEGIN IMMEDIATE;\n" + "\n".join(pending_scripts)
        
        try:
            self.conn.executescript(full_script)
            
            # Validate FKs before committing
            cursor.execute("PRAGMA foreign_key_check;")
            violations = cursor.fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"Foreign key violations detected after migration: {violations}")
                
            self.conn.commit()
            logger.info(f"Successfully applied {len(applied_names)} migrations in a single transaction: {', '.join(applied_names)}")
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Failed to apply migrations: {e}")
            raise
        finally:
            # Restore FK state (must be done outside of transaction)
            state_str = "ON" if fk_state else "OFF"
            self.conn.execute(f"PRAGMA foreign_keys = {state_str};")

    def _load_migration_files(self) -> list[tuple[int, Path]]:
        if self._migrations is not None:
            return self._migrations
            
        migrations = []
        if not self.migrations_dir.exists():
            self._migrations = migrations
            return migrations

        for path in self.migrations_dir.glob("*.sql"):
            if path.name.startswith("_"):
                continue
                
            match = re.match(r"^(\d+)_.*\.sql$", path.name)
            if match:
                version = int(match.group(1))
                migrations.append((version, path))
            else:
                logger.warning(f"Ignoring non-conforming SQL file: {path.name}")
                
        migrations.sort(key=lambda x: x[0])
        self._validate_sequence(migrations)
        self._migrations = migrations
        return migrations

    def _validate_sequence(self, migrations: list[tuple[int, Path]]) -> None:
        versions = [v for v, _ in migrations]
        if len(versions) != len(set(versions)):
            raise ValueError("Duplicate migration version numbers detected.")
        expected = list(range(1, max(versions) + 1)) if versions else []
        if versions != expected:
            raise ValueError(f"Migration sequence has gaps: found {versions}, expected {expected}")


