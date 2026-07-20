CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    schedule_channel_id INTEGER UNIQUE,
    management_channel_id INTEGER UNIQUE,
    planning_days INTEGER,
    priority_threshold INTEGER,
    compute_timeout REAL,
    step_minutes INTEGER
) STRICT;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id INTEGER UNIQUE NOT NULL,
    has_consented_llm INTEGER NOT NULL DEFAULT 0,
    schedule_id INTEGER DEFAULT NULL,
    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
) STRICT;
