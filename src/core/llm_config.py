"""LLM configuration: models, model priorities and system instructions.

`phrases.json` holds text a reader sees: localized, keyed by guild, rewritten by
an operator who is choosing wording. The LLM's own configuration happens to have
lived there too, and it is none of those things -- nobody reads a rate limit,
and a system instruction is written for a model rather than for a person.

It lives in `llm_config.json` instead, resolved against the process CWD like
`phrases.json` (so `src/llm_config.json`, because the bot is started from
`src/`) and shaped the same way: a `global` section plus one optional section
per guild id, deep-merged over global at load time.

An instruction is a long text a guild owner rewrites, so it may be written
either inline or as `{"file": "prompts/system.md"}`, resolved against the
directory holding the config. Every prompt file but the schedule agent's is
gitignored: the agent's instruction describes tools rather than a particular
server, which makes it the only one this repository can carry.

Prompt files are re-read when their mtime changes, so editing one takes effect
without a reload; the config file itself needs `/reload_llm_config`.
"""

import json
import logging
from pathlib import Path

import core.cache
from core.utils import deep_merge

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "llm_config.json"

# What `{"file": ...}` references resolve against -- the directory holding the
# loaded config, so a checkout's `prompts/` sits next to it. Until a config is
# loaded this is the CWD, which is what keeps a `default_file` working on a
# checkout carrying no config at all.
_base_dir = Path()

# path -> (mtime, text)
_prompt_cache: dict[Path, tuple[float, str]] = {}


def load_llm_config(path: str | Path = DEFAULT_CONFIG_FILE) -> bool:
    """
    Read the config into `core.cache._llm_config`, guild sections merged over global.

    Returns False when the file is missing or unparsable, leaving whatever was
    loaded before in place -- a typo in a hand-edited file must not wipe a
    running bot's models.
    """
    global _base_dir

    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error("Error loading LLM config from %s: %s", path, e)
        return False

    if not isinstance(raw, dict):
        logger.error("LLM config %s must be a JSON object, got %s", path, type(raw).__name__)
        return False

    global_section = raw.get("global", {})
    if not isinstance(global_section, dict):
        global_section = {}

    merged = {
        key: (value if key == "global" else deep_merge(global_section, value))
        for key, value in raw.items()
        if isinstance(value, dict)
    }

    _base_dir = path.resolve().parent
    _prompt_cache.clear()

    core.cache._llm_config.clear()
    core.cache._llm_config.update(merged)

    logger.info("Loaded LLM config from %s (%d section(s))", path, len(merged))
    return True


def get_llm_config(guild_id=None) -> dict:
    """The config section for one guild, falling back to the global one."""
    global_config = core.cache._llm_config.get("global", {})
    if guild_id is None:
        return global_config

    return core.cache._llm_config.get(str(guild_id), global_config)


def get_models() -> list[dict]:
    """
    The configured models as raw dicts, ordered best/most expensive first.

    Global only: a client pool is keyed by API token, not by guild, so there is
    no guild whose section could sensibly own a different set of rate limits.
    """
    models = get_llm_config().get("models")
    if not isinstance(models, list):
        return []

    return [model for model in models if isinstance(model, dict) and "name" in model]


def get_priority(name: str, guild_id=None) -> list[str]:
    """Model names one kind of call prefers, in order. Empty means 'no preference'."""
    priorities = get_llm_config(guild_id).get("priorities", {})
    names = priorities.get(name) if isinstance(priorities, dict) else None

    if not isinstance(names, list):
        return []

    return [str(model_name) for model_name in names]


def get_instruction(key: str, guild_id=None, *, default_file: str | None = None, default: str = "") -> str:
    """
    One system instruction, written either inline or as `{"file": "..."}`.

    `default_file` is the prompt this repository ships for the key, read when the
    config names none -- which is how the schedule agent keeps working on a
    checkout with no `llm_config.json` yet.
    """
    instructions = get_llm_config(guild_id).get("instructions", {})
    value = instructions.get(key) if isinstance(instructions, dict) else None

    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        text = _read_prompt_file(value.get("file"))
        return text if text is not None else default

    if default_file:
        text = _read_prompt_file(default_file)
        if text is not None:
            return text

    return default


def _read_prompt_file(file_name) -> str | None:
    """Read a prompt file relative to the config, re-reading it only when it changed."""
    if not file_name or not isinstance(file_name, str):
        return None

    path = _base_dir / file_name

    try:
        mtime = path.stat().st_mtime
        cached = _prompt_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        text = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.error("Cannot read prompt file %s: %s", path, e)
        return None

    _prompt_cache[path] = (mtime, text)
    return text
