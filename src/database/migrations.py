import sqlite3
import logging
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import re

logger = logging.getLogger(__name__)

_MIGRATION_PATTERN = re.compile(r"^(\d+)_.*\.(sql|py)$")


class MigrationRunner:
    """SQLite schema migration runner using PRAGMA user_version.

    Supports two types of migration files:

    - **SQL migrations** (``N_name.sql``): Plain SQL scripts executed via
      ``executescript``. Consecutive SQL migrations are batched into a
      single transaction for efficiency.

    - **Python migrations** (``N_name.py``): Python modules that expose an
      ``upgrade(conn: sqlite3.Connection) -> None`` function. Each Python
      migration runs in its own transaction. Use these when you need
      procedural logic, data transformations, or anything that is hard to
      express in pure SQL.

    Migration files must follow the naming convention
    ``<version>_<description>.<ext>`` where ``<version>`` is a positive
    integer and ``<ext>`` is either ``sql`` or ``py``.  Each version number
    must be unique across both file types.
    """

    def __init__(self, conn: sqlite3.Connection, migrations_dir: Path = None):
        self.conn = conn
        if migrations_dir is None:
            self.migrations_dir = Path(__file__).resolve().parent
        else:
            self.migrations_dir = Path(migrations_dir)

        self._migrations: list[tuple[int, Path]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_version(self) -> int:
        return self.conn.execute("PRAGMA user_version;").fetchone()[0]

    def get_latest_version(self) -> int:
        migrations = self._load_migrations()
        return migrations[-1][0] if migrations else 0

    def migrate(self) -> None:
        current = self.get_current_version()
        latest = self.get_latest_version()

        if current == 0:
            current = self._handle_legacy_db()

        if current >= latest:
            logger.debug(f"Database is up to date (version {current}).")
            return

        logger.info(f"Database requires migration (current: {current}, latest: {latest}).")
        self._apply_pending(current)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_legacy_db(self) -> int:
        """Detect a legacy (pre-migration) database and stamp it as v1."""
        has_tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules';"
        ).fetchone()

        if has_tables:
            logger.info("Detected legacy database (has tables but user_version=0). Setting user_version=1.")
            self.conn.execute("PRAGMA user_version = 1;")
            self.conn.commit()
            return 1

        logger.info("Initializing fresh database from migrations.")
        return 0

    @contextmanager
    def _fk_guard(self):
        """Context manager: saves FK state, disables FKs, restores on exit."""
        fk_was_on = self.conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        self.conn.execute("PRAGMA foreign_keys = OFF;")
        try:
            yield
        finally:
            self.conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_was_on else 'OFF'};")

    def _check_fk_violations(self, context: str) -> None:
        """Raise if any FK violations exist; *context* is used in the message."""
        violations = self.conn.execute("PRAGMA foreign_key_check;").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"Foreign key violations after {context}: {violations}"
            )

    # ------------------------------------------------------------------
    # Applying migrations
    # ------------------------------------------------------------------

    def _apply_pending(self, current: int) -> None:
        migrations = self._load_migrations()
        pending = [(v, p) for v, p in migrations if v > current]
        if not pending:
            return

        applied: list[str] = []

        with self._fk_guard():
            i = 0
            while i < len(pending):
                if pending[i][1].suffix == ".py":
                    self._apply_python(pending[i][0], pending[i][1])
                    applied.append(pending[i][1].name)
                    i += 1
                else:
                    # Collect consecutive SQL migrations into a batch
                    batch: list[tuple[int, Path]] = []
                    while i < len(pending) and pending[i][1].suffix == ".sql":
                        batch.append(pending[i])
                        i += 1
                    self._apply_sql_batch(batch)
                    applied.extend(p.name for _, p in batch)

        logger.info(f"Successfully applied {len(applied)} migration(s): {', '.join(applied)}")

    def _apply_sql_batch(self, batch: list[tuple[int, Path]]) -> None:
        """Apply consecutive SQL migrations in a single transaction."""
        parts = []
        for version, path in batch:
            logger.info(f"Preparing migration: {path.name}")
            sql = path.read_text(encoding="utf-8")
            parts.append(f"-- Migration {version}\n{sql}\nPRAGMA user_version = {version};")

        script = "BEGIN IMMEDIATE;\n" + "\n".join(parts)

        try:
            self.conn.executescript(script)
            self._check_fk_violations(f"SQL batch ({', '.join(p.name for _, p in batch)})")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            logger.error(f"Failed to apply SQL batch: {', '.join(p.name for _, p in batch)}")
            raise

    def _apply_python(self, version: int, path: Path) -> None:
        """Apply a single Python migration in its own transaction.

        The module must expose ``upgrade(conn: sqlite3.Connection) -> None``.
        The function receives the connection with foreign keys disabled and
        an ``IMMEDIATE`` transaction already started.
        """
        logger.info(f"Applying Python migration: {path.name}")

        spec = importlib.util.spec_from_file_location(f"olive_migration_{version}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        upgrade_fn = getattr(module, "upgrade", None)
        if upgrade_fn is None:
            raise ValueError(
                f"Python migration {path.name} must define an "
                f"'upgrade(conn: sqlite3.Connection) -> None' function."
            )

        try:
            self.conn.execute("BEGIN IMMEDIATE;")
            upgrade_fn(self.conn)
            self.conn.execute(f"PRAGMA user_version = {version};")
            self._check_fk_violations(f"Python migration {path.name}")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            logger.error(f"Failed to apply Python migration: {path.name}")
            raise

    # ------------------------------------------------------------------
    # Discovery & validation
    # ------------------------------------------------------------------

    def _load_migrations(self) -> list[tuple[int, Path]]:
        if self._migrations is not None:
            return self._migrations

        migrations: list[tuple[int, Path]] = []
        if not self.migrations_dir.exists():
            self._migrations = migrations
            return migrations

        for path in sorted(self.migrations_dir.iterdir()):
            if path.name.startswith("_") or path.suffix not in (".sql", ".py"):
                continue

            match = _MIGRATION_PATTERN.match(path.name)
            if match:
                migrations.append((int(match.group(1)), path))
            elif path.suffix == ".sql":
                # Warn about .sql files that don't follow the naming convention;
                # .py files without a version prefix are normal (e.g. migrations.py).
                logger.warning(f"Ignoring non-conforming SQL file: {path.name}")

        migrations.sort(key=lambda x: x[0])
        self._validate_sequence(migrations)
        self._migrations = migrations
        return migrations

    @staticmethod
    def _validate_sequence(migrations: list[tuple[int, Path]]) -> None:
        versions = [v for v, _ in migrations]
        if len(versions) != len(set(versions)):
            raise ValueError("Duplicate migration version numbers detected.")
        expected = list(range(1, max(versions) + 1)) if versions else []
        if versions != expected:
            raise ValueError(f"Migration sequence has gaps: found {versions}, expected {expected}")
