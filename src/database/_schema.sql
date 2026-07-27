/* 
  Complete database schema.
  Intended for testing compliance with this schema during migration 
  and to facilitate development.
*/

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
    has_consented_llm INTEGER NOT NULL DEFAULT 0, -- boolean
    schedule_id INTEGER DEFAULT NULL,
    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
) STRICT;

CREATE TABLE IF NOT EXISTS llm_token_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    context_tokens INTEGER NOT NULL DEFAULT 64000,
    reserved_system_tokens INTEGER NOT NULL DEFAULT 6000,
    reserved_memory_tokens INTEGER NOT NULL DEFAULT 32000,
    reserved_response_tokens INTEGER NOT NULL DEFAULT 5000
) STRICT;
