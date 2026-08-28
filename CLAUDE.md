# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

O.L.I.V.E. is a Discord bot (disnake) that acts as a hub for several subsystems: an LLM assistant on Google GenAI, a CP-SAT scheduling engine, and an inflation calculator. Two of these live in git submodules under `src/modules/` and are vendored, not written here.

## Setup, running, checks

The bot runs from inside `src/`; the checks run from the repo root. Which directory you are in decides what the code finds:

```bash
git submodule update --init --recursive   # tests for inflation need these present
pip install -r requirements.txt
cp settings.py.example src/settings.py    # plus tokens.json
cd src && python main.py
```

| What | Resolved against | So it lands in |
| --- | --- | --- |
| `config.ini`, `phrases.json`, `llm_limits_state{role}.json`, `olive.sqlite3` | process CWD | `src/`, because that is where the bot is started |
| the `cogs` extension path | process CWD | `src/cogs/` |
| `tokens.json` | `core/token_manager.py`'s own `__file__` | always `src/tokens.json` |
| the providers' JSON (`data/…`) | `modules/*_provider.py`'s own `__file__` | always repo-root `data/` |
| `python -m unittest discover`, `ruff`, `black` | process CWD | repo root — running them from `src/` finds nothing |

The last three rows are the ones worth remembering: the bottom two exceptions do not move when you `cd`, and the checks do.

`src/settings.py`, `src/tokens.json`, `src/phrases.json`, `config.ini`, `data/` and `*.sqlite3` are all gitignored and absent from a fresh checkout. Code must tolerate a missing `phrases.json` (every lookup carries a fallback string) but `settings.py` is imported directly and its absence is fatal.

```bash
python -m unittest discover                 # from repo root; CI runs exactly this
python -m unittest tests.test_inflation_report                       # one module
python -m unittest tests.test_migrations.TestMigrations.test_name    # one test
ruff check .                                # CI pins ruff==0.16.2
black .                                     # line-length 120, auto-applied by CI
```

Both submodule directories are excluded from ruff and black — never reformat or lint-fix inside them.

The three workflows do not cover the same branches: `unittest.yml` runs on every branch and PR (Python 3.14, `pip install -r requirements.txt`, submodules checked out), while `code_check.yml` (ruff, 3.12) and `black-formatter.yml` (opens an auto-format PR) only fire on `main` and `mk*`. So a feature branch gets tests but no lint feedback until it targets one of those — run `ruff check .` locally.

Tests insert `src/` into `sys.path` themselves and stub `settings`, so they run without a config. What they do need is uneven — a module either imports cleanly on a bare checkout or fails at import time, so half the suite is unrunnable until the submodules are initialized:

| Test module | submodules | `ortools` | `disnake` |
| --- | :-: | :-: | :-: |
| `test_inflation_formatter` | – | – | – |
| `test_llm_client` | – | – | – |
| `test_llm_rate_limiter` | – | – | – |
| `test_migrations` | – | – | – |
| `test_phrases` | – | – | – |
| `test_schd_item_formatters` | – | – | – |
| `test_schedule_pagination` | – | – | – |
| `test_schedule_stats` | – | – | – |
| `test_schedule_timeline` | – | – | – |
| `test_llm_context_manager` | – | – | ✓ |
| `test_paged_message` | – | – | ✓ |
| `test_personal_channels` | – | – | ✓ |
| `test_inflation_phrases` | ✓ | – | ✓ |
| `test_inflation_provider` | ✓ | – | ✓ |
| `test_inflation_replies` | ✓ | – | ✓ |
| `test_inflation_report` | ✓ | – | ✓ |
| `test_schedule_engine` | ✓ | ✓ | ✓ |
| `test_schedule_timeblocks` | ✓ | ✓ | ✓ |
| `test_schedule_ui` | ✓ | ✓ | ✓ |

The dependencies are transitive, not written in the tests: anything reaching `inflation_provider` pulls in the vendored calculator and, through `core.personal_channels`, `disnake`; `schedule_engine` reaches `disnake` the same way, through `schedule_provider` → `core.personal_channels`. `ortools` travels further than it looks: the vendored scheduler's package `__init__` re-exports `Scheduler`, so importing `data_structs` through it — which `schedule_models` does, and everything above it with it — needs the solver installed even where nothing solves. Keeping a suite in the top block therefore means keeping the module under test away from those, which is why `test_schedule_stats` builds its temporary database with the real migrations rather than through a provider, and why `tests/inflation_fixtures.py` — the shared report/record/deposit builders and the fake provider — is deliberately free of `settings`. `llm_rate_limiter` is the easy end of that: it imports `dataclasses` and nothing else, and every method that reads the clock takes `now` as an argument, so its suite needs neither stubs nor a patched clock. `test_llm_client` is the other shape of the same problem: `llm_client` imports the Google GenAI SDK at module level, so the suite imports it when it is installed and substitutes a stub in `sys.modules` when it is not — a native dependency can abort that import with a `BaseException`, so the guard catches more than `ImportError`. It drives `get_interaction` against a fake API object and a hand-moved clock, never the network.

When writing one: `tests/` is a real package, so a test module importing a sibling writes `from tests.inflation_fixtures import ...`, and the suite is run from the repo root. Stub `settings` in the module itself rather than relying on another one having done it — discovery order is not a contract, and every module has to run alone. Anything exercising a provider for real must first point `inflation_provider.get_base_data_dir` at a temporary directory: it is the single root the record files, the rates file and both channel registries hang off, and `settings.inflation_data_dir` redirects only the first two — a test that patches nothing writes into the repository's own gitignored `data/`.

## Architecture

`src/main.py` builds `core.bot.OliveBot`, calls `load_phrases()`, then `load_extensions("cogs")`, which recursively imports every `.py` under `src/cogs/` (subpackages like `cogs/schedule/` have empty `__init__.py`; each leaf module has its own `setup(bot)`). `OliveBot.load_extension` is overridden to honour `settings.cogs_blacklist` (dotted name minus the `cogs.` prefix, e.g. `"embeds.battery"`) and to record load times in `core.cache.active_cogs_list`.

Layering is `cogs/` over `modules/` over `core/` — dependencies point downward only, and a layer may reach any layer below it, not just the next one down. `core/` imports nothing from `modules/` or `cogs/`. Cogs use `core` directly and always do (every cog imports it; half of them never touch `modules` at all — see the three cog bases below, which are `core` classes a cog subclasses). Cogs hold Discord commands and listeners only; domain logic and all access to the vendored submodules go through a *provider* in `modules/` (`ScheduleProvider`, `inflation_provider`). No cog imports `modules.automatic_timetable_py` or `modules.inflation_calculator` directly. The schedule cog is the exception to a cog holding no assembly: it composes the chain below, because what a page may hold depends on the frame it is about to render (see below).

The inflation rendering below that provider is four modules, and the line between them is what each may know: `inflation_formatter` knows numbers and text only — no phrases, no `settings`, no filesystem, which is what keeps its suite dependency-free; `inflation_phrases` knows `phrases.json` and returns one localized fragment per call, composing nothing; `inflation_report` builds the report a reader watches in their channel (paginated) and its single-message form for a guild; `inflation_replies` builds the one-off answers to a slash command, which Discord refuses rather than truncates when they run long, so they all go through `fit_into_message`. `report` and `replies` are siblings and must not import each other — anything both need belongs one layer down.

The schedule rendering is a chain of named steps rather than a tuple handed down it: `schedule_engine.solve_schedule()` returns a `SolvedSchedule` (items plus what did not fit), `schedule_timeline.group_into_days()` turns its items into `ScheduleDay`s of chronological timeline blocks, and `schedule_pagination.paginate_days()` cuts those days into pager-sized pages. `schedule_formatter` is now only the agent's flat-text rendering of the same two calls. `column_widths()` is measured once over the whole schedule and handed down, so the id column and the arrow's shaft — which is what carries a routine's `[Fxd]`/`[Flb]` marker, out of the way of the names it used to blur into — do not shift when the reader turns the page; a gap indicator rides on the end time that opened it, so the next item's start time keeps a bare line of its own — what a reader looks for first is where the next thing begins. The cut is chronological and the bottom-up flip happens per page afterwards — flipping first is what used to put the evening on a day's first page. How much a page may hold is not a constant: the cog prices its own frame by rendering it around an empty body — the status header comes from `phrases.json` and an operator can rewrite it to any length — and `page_char_limit()` hands over what Discord's 2000 leaves. The "didn't fit" lists below the schedule grow with the user's data, so `build_notes()` packs them into one line within what is left over a page's minimum — short labels, id runs written as ranges, `+N` for what was cut. Neither `schedule_timeline` nor `schedule_pagination` imports anything, so their suites need neither the submodules nor `disnake`.

`core/cache.py` is the shared mutable state between cogs — embeds awaiting publication, the `config.ini` lock, the LLM client pool, loaded phrases, channel pager state. Cross-cog communication otherwise happens through disnake's dispatcher: a command mutates data and calls `bot.dispatch("schedule_update", channel_id)`; the UI cog listens with `@commands.Cog.listener("on_schedule_update")`. This is what lets the writer and the renderer live in different files.

### Three reusable cog bases

Most cogs are thin subclasses of one of these — prefer extending them over hand-rolling a `tasks.loop`:

- `core/embed_cog.py` `BaseEmbedCog` — everything in `cogs/embeds/`. Subclass declares `embed_key`, `phrases_section`, `settings_key` and overrides `get_data()`; the base handles the interval, phrase lookup, footer, errors, and writes the built embed into `cache.embeds_to_send`. Embed cogs never send messages. `cogs/statistic_message_loop.py` is the single publisher: every 10s it collects `cache.embeds_to_send`, filters per guild via `settings.embeds_blacklist`, and edits one eternal message per configured channel.
- `core/channel_loop.py` `PersonalChannelLoopCog` — the schedule and inflation loops. Declares a registry plus init/update event names and dispatches them on a slow tick.
- `core/paged_message.py` — modules supply a `PageSource`; paging, buttons and change-detection are inherited. Exists because a report can exceed Discord's 10-embeds / 6000-char limits. A source whose per-page components come and go pads them with `blank_buttons()` up to what its busiest page needs: Discord lays components out in rows of five, so a page with fewer buttons than the last one drags the pager up the message as the reader turns pages.

`ResilientTaskHandler` (`core/task_handler.py`) is wired into these loops and does exponential backoff (5s → 150s) on Discord 5xx and network errors.

### Personal channel pairs

`core/personal_channels.py` implements the pattern both the schedule and inflation subsystems use: each user gets a read-only channel holding an eternal webhook message plus a management channel for commands. `PersonalChannelRegistry` is a JSON registry that **preserves keys it does not recognise**, which is how modules stash per-owner settings (solver options, `view_mode`) next to the channel ids without a file of their own. This module is deliberately free of `settings` and phrases imports so it stays unit-testable — pass paths, limits and categories in.

### Persistence

Two stores, split by kind of data:

- SQLite (`core/database.py`, module-level singleton `db`) for users, LLM consent and token budgets. `db`'s connection is lazy: importing the module (or anything that holds a reference to `db`, like `llm_consent_manager` or `llm_token_budget`) does not touch the file or run migrations — that happens on the first `db.execute()`/`db.executemany()`/`db.conn` access. Set `OLIVE_DB_PATH` (e.g. to `:memory:`) to point a `DatabaseManager` at something other than `olive.sqlite3`, which is what a test needing a working `db` without touching disk should do rather than relying on the file the global singleton defaults to. Schema evolves through `src/database/migrations.py` using `PRAGMA user_version`; add `N_name.sql` or `N_name.py` (exposing `upgrade(conn)`) in `src/database/`, keep `_schema.sql` in sync — a test asserts migrated schema matches it.
- JSON files under repo-root `data/` for schedules, inflation records and channel registries, written by the providers.

A stored time block says `"repeat": "once" | "daily" | "weekly"`, plus `"weekdays"` (0=Monday) when weekly — the same word a routine uses, and the same grammar the vendored scheduler's own `data.json` takes. The solver's dataclass spells it as `daily: bool` plus `weekdays`, because everything below it counts in steps and never sees a calendar; `modules/schedule_models.py` holds both the vocabulary and `block_repeat()`, which reads one back off the other. Files predating the `repeat` word carry a `daily` bool instead, which `_dict_to_timeblock` still reads — defaulting rather than reading it would turn every one-time block into a daily one — and `src/scripts/migrate_timeblock_repeat.py` rewrites in place.

`modules/schedule_stats.py` is the exception to a module reaching the database directly at import: `core.database` opens the file and runs its migrations at import time, so the stats module imports it on first use instead — otherwise every test that reaches the engine would leave an `olive.sqlite3` next to the code. `use()` points it at another database, which is how its suite gets a temporary one.

### Phrases

Nearly all user-facing text comes from `phrases.json`, keyed by guild id with a `"global"` fallback; guild sections are deep-merged over global at load time. Use `get_phrases(guild_id)` when a guild is in scope and `get_phrases()` otherwise, always with an inline fallback string. `format_phrase()` / `format_embed_data()` fall back rather than raise when a hand-edited placeholder does not match. Editable live via `/edit_phrases` + `/reload_phrases` — but only the text looked up at call time. A cog that reads a `*_cmd` section into a module-level dict (`cogs/inflation/tools.py`, `cogs/schedule/tools.py`) does so at import, and those strings are the command and parameter descriptions Discord already registered, so reloading phrases cannot change them.

### LLM subsystem

`modules/llm_client.py` wraps Google GenAI with a client pool keyed by role (`default`, `private` — separate API keys from `tokens.json`) and a rate limiter whose model list comes from `phrases.json` → `olive` → `models`, ordered best-to-cheapest. Persistent state lives in `llm_limits_state{role}.json` (CWD-relative, one file per role) and in the `llm_token_budgets` table — rows `default` and `private`, seeded by migration 2 and edited live with `/token_budget set`. `modules/schedule_agent.py` is an agentic loop over `ScheduleAgentTools` (capped at `MAX_ITERATIONS`, changes revertible for 15 minutes) using the `private` role.

## Conventions

- Read tunables with `getattr(settings, "name", default)` — operators' `settings.py` files predate newly added keys.
- Only the eternal message in a personal channel has a pager. A slash-command reply is a single message, and Discord refuses one over 2000 characters instead of truncating it — so anything whose length grows with the user's data has to be measured and trimmed, not hoped about (`inflation_replies.fit_into_message`).
- `PERF203` (try/except inside a loop) is ignored in ruff config on purpose: per-item error isolation is intended.
- Docs are bilingual, `docs/EN/` and `docs/UK/`; `docs/UK/architecture.md` is the fuller architecture write-up but predates the schedule/inflation subsystems.
