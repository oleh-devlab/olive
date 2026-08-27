"""How much time the solver has spent, and on whose schedules.

One row per day per user, added to in place. A solve costs one UPSERT; a window
the embed shows is a SUM over at most a few hundred rows, which SQLite answers
in well under a millisecond — so nothing here keeps a running total that could
drift away from the rows it came from.

The day is the bot's own, not UTC: a reader comparing "today" against their
schedule is looking at the same clock the schedule was drawn on.
"""

import datetime
import logging

from core.time_utils import tz

logger = logging.getLogger(__name__)

_TABLE = "schedule_solve_stats"

_RECORD = f"""
    INSERT INTO {_TABLE} (day, user_id, seconds, solves) VALUES (?, ?, ?, 1)
    ON CONFLICT(day, user_id) DO UPDATE SET seconds = seconds + excluded.seconds, solves = solves + 1
"""

_database = None


def use(database) -> None:
    """Point the recorder at a database — the app's own, or a test's temporary one."""
    global _database
    _database = database


def _db():
    """The shared database, imported on first use.

    `core.database` opens the file and runs its migrations at import time, so
    importing it at module scope would have every test that reaches the engine
    create an `olive.sqlite3` next to the code.
    """
    global _database
    if _database is None:
        from core.database import db

        _database = db

    return _database


def today() -> str:
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _day_before(days: int) -> str:
    """The oldest day a window of `days` days reaches back to, today included."""
    return (datetime.datetime.now(tz) - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")


def record(user_id: int, seconds: float) -> None:
    """Add one solve to today's row for this user.

    Statistics must never cost a user their schedule, so a database that refuses
    the write is logged and shrugged off.
    """
    if seconds <= 0:
        # Nothing was solved — no tasks, or the solver never ran.
        return

    try:
        _db().execute(_RECORD, (today(), user_id, seconds))
    except Exception as e:
        logger.error(f"Failed to record a {seconds:.4f}s solve for user {user_id}: {e}")


def totals(days: int | None = None) -> tuple[float, int]:
    """Seconds spent and solves run over the last `days` days, or over everything."""
    query = f"SELECT COALESCE(SUM(seconds), 0), COALESCE(SUM(solves), 0) FROM {_TABLE}"
    params: tuple = ()

    if days is not None:
        query += " WHERE day >= ?"
        params = (_day_before(days),)

    try:
        seconds, solves = _db().execute(query, params)[0]
    except Exception as e:
        logger.error(f"Failed to read solve totals: {e}")
        return 0.0, 0

    return float(seconds), int(solves)


def top_users(limit: int = 3, days: int | None = None) -> list[tuple[int, float, int]]:
    """The users whose schedules cost the most time, dearest first."""
    query = f"SELECT user_id, SUM(seconds) AS spent, SUM(solves) FROM {_TABLE}"
    params: tuple = ()

    if days is not None:
        query += " WHERE day >= ?"
        params = (_day_before(days),)

    query += " GROUP BY user_id ORDER BY spent DESC LIMIT ?"

    try:
        rows = _db().execute(query, (*params, limit))
    except Exception as e:
        logger.error(f"Failed to read the top solve users: {e}")
        return []

    return [(int(user_id), float(seconds), int(solves)) for user_id, seconds, solves in rows]


def counting_since() -> str | None:
    """The first day anything was recorded, or None while nothing has been."""
    try:
        rows = _db().execute(f"SELECT MIN(day) FROM {_TABLE}")
    except Exception as e:
        logger.error(f"Failed to read the first recorded day: {e}")
        return None

    return rows[0][0] if rows and rows[0][0] else None


def format_duration(seconds: float) -> str:
    """`2.4s`, `3m 20s`, `1h 05m` — the unit a reader can hold in their head."""
    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {remainder:02d}s"

    hours, minutes = divmod(minutes, 60)

    return f"{hours}h {minutes:02d}m"


def format_day(day: str | None) -> str:
    """A stored `YYYY-MM-DD` as the `dd.mm.YYYY` the rest of the schedule uses."""
    if not day:
        return "N/A"

    try:
        return datetime.date.fromisoformat(day).strftime("%d.%m.%Y")
    except ValueError:
        return day
