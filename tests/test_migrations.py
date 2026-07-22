import unittest
import sqlite3
import sys
from pathlib import Path

# Setup path so we can import from src
# TODO: fix paths
src_root = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_root))

from database.migrations import MigrationRunner  # noqa: E402


def dump_schema(conn: sqlite3.Connection) -> str:
    """Helper to dump the semantic schema of a database using PRAGMAs."""
    cursor = conn.cursor()

    # Optional: fetch table flags if SQLite supports PRAGMA table_list (>= 3.37.0)
    table_flags = {}
    if sqlite3.sqlite_version_info >= (3, 37, 0):
        cursor.execute("PRAGMA table_list")
        for row in cursor.fetchall():
            # schema, name, type, ncol, wr, strict
            if row[2] == "table":
                table_flags[row[1]] = {"wr": row[4], "strict": row[5]}

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    schema_dump = []
    for table in tables:
        flags = table_flags.get(table, {})
        flag_str = f" [STRICT:{flags.get('strict', 0)} WR:{flags.get('wr', 0)}]" if flags else ""
        schema_dump.append(f"TABLE {table}{flag_str}:")

        # Columns: cid, name, type, notnull, dflt_value, pk
        cursor.execute(f"PRAGMA table_info({table})")
        for col in cursor.fetchall():
            schema_dump.append(f"  COL: {col[1]} | type:{col[2]} | notnull:{col[3]} | dflt:{col[4]} | pk:{col[5]}")

        # Foreign Keys: id, seq, table, from, to, on_update, on_delete, match
        cursor.execute(f"PRAGMA foreign_key_list({table})")
        for fk in sorted(cursor.fetchall(), key=lambda x: (x[0], x[1])):
            schema_dump.append(f"  FK: {fk[3]} -> {fk[2]}({fk[4]}) ON UPDATE {fk[5]} ON DELETE {fk[6]}")

        # Indexes: seq, name, unique, origin, partial
        cursor.execute(f"PRAGMA index_list({table})")
        for idx in sorted(cursor.fetchall(), key=lambda x: x[1]):
            # Ignore sqlite auto-indexes for internal use
            if idx[1].startswith("sqlite_autoindex_"):
                continue
            schema_dump.append(f"  IDX: {idx[1]} | unique:{idx[2]}")
            cursor.execute(f"PRAGMA index_info('{idx[1]}')")
            for ic in cursor.fetchall():
                schema_dump.append(f"    IDX_COL: {ic[2]}")

    # Views and Triggers
    cursor.execute("SELECT type, name, sql FROM sqlite_master WHERE type IN ('view', 'trigger') ORDER BY type, name")
    for row in cursor.fetchall():
        sql_clean = " ".join(row[2].split()) if row[2] else ""
        schema_dump.append(f"{row[0].upper()} {row[1]}: {sql_clean}")

    return "\n".join(schema_dump)


class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.migrations_dir = src_root / "database"

    def test_migrations_match_baseline_schema(self):
        """
        Verify that applying all migrations sequentially yields
        the exact same database schema as executing the baseline _schema.sql.
        """
        # DB A: build by replaying the full migration chain
        conn_a = sqlite3.connect(":memory:")
        runner = MigrationRunner(conn_a, migrations_dir=self.migrations_dir)
        runner.migrate()

        # DB B: build by executing the reference schema file directly
        conn_b = sqlite3.connect(":memory:")
        schema_path = self.migrations_dir / "_schema.sql"
        self.assertTrue(schema_path.exists(), "Reference schema _schema.sql is missing")
        schema_sql = schema_path.read_text(encoding="utf-8")
        conn_b.executescript(schema_sql)

        schema_a = dump_schema(conn_a)
        schema_b = dump_schema(conn_b)

        self.assertEqual(
            schema_a, schema_b, "The schema produced by migrations does not match the baseline _schema.sql"
        )


if __name__ == "__main__":
    unittest.main()
