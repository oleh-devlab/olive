CREATE TABLE llm_token_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    context_tokens INTEGER NOT NULL DEFAULT 64000,
    reserved_system_tokens INTEGER NOT NULL DEFAULT 6000,
    reserved_memory_tokens INTEGER NOT NULL DEFAULT 32000,
    reserved_response_tokens INTEGER NOT NULL DEFAULT 5000
) STRICT;

INSERT INTO llm_token_budgets (name) VALUES ('default');
INSERT INTO llm_token_budgets (name, context_tokens, reserved_system_tokens, reserved_memory_tokens, reserved_response_tokens)
    VALUES ('private', 64000, 6000, 32000, 5000);
