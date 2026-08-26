CREATE TABLE schedule_solve_stats (
    day TEXT NOT NULL,               -- 'YYYY-MM-DD', the bot's timezone
    user_id INTEGER NOT NULL,        -- whose schedule was solved
    seconds REAL NOT NULL DEFAULT 0,
    solves INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
) STRICT;
