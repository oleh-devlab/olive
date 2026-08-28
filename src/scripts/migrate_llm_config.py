"""One-off move of the LLM's configuration out of `phrases.json` into `llm_config.json`.

`phrases.json` is for text a reader sees. Model lists, rate limits, model
priorities and system instructions were living there too -- nobody reads a rate
limit, and a system instruction is written for a model rather than for a person.
This script takes them out, writing:

    src/llm_config.json      models, priorities, and pointers to the prompts
    src/prompts/<key>.md     one file per instruction, per section

and leaving the phrases file with only phrases. `no_consent_placeholder` is not
migrated: only the model ever reads it, so its wording now lives in the code
(`modules/llm_message_formatter.NO_CONSENT_PLACEHOLDER`) and the key is dropped.

Nothing in the bot reads the old keys any more, so run this before starting the
new version. From the repo root:

    python src/scripts/migrate_llm_config.py

The phrases file is rewritten in place with a `.bak` copy left beside it. The
script refuses to overwrite an existing `llm_config.json` or an existing prompt
file, so a second run cannot quietly undo hand-edits made after the first.
"""

import json
import shutil
import sys
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent

# Old key in the `olive` section -> new name under `priorities`.
PRIORITY_KEYS = {
    "test_models_priority": "response_gate",
    "schedule_agent_models_priority": "schedule_agent",
}

# Old key in the `olive` section -> new name under `instructions`, each written
# to its own file under `prompts/`.
INSTRUCTION_KEYS = {
    "system_instruction": "system",
    "system_instruction_addition": "system_addition",
    "test_instruction_addition": "response_gate_addition",
}

# Read by the model alone, so it belongs in the code rather than in either file.
DROPPED_KEYS = ["no_consent_placeholder"]

# Shipped by the repository, and the only instruction that can be: it describes
# the agent's tools rather than a server.
AGENT_PROMPT_FILE = "prompts/schedule_agent.md"


def prompt_file_name(instruction_key: str, section: str) -> str:
    """`prompts/system.md` for global, `prompts/system.123.md` for one guild."""
    suffix = "" if section == "global" else f".{section}"
    return f"prompts/{instruction_key}{suffix}.md"


def migrate_section(olive: dict, section: str, prompts: dict[str, str]) -> dict:
    """
    Pull one phrases section's `olive` keys into a new config section.

    Mutates `olive`, removing what moved out; collects prompt texts into
    `prompts` (path -> text) rather than writing them, so nothing lands on disk
    before every section has been read successfully.
    """
    config: dict = {}

    models = olive.pop("models", None)
    legacy_name = olive.pop("model_name", None)
    if isinstance(models, list) and models:
        config["models"] = models
    elif legacy_name:
        # The single-model spelling the client used to fall back to.
        config["models"] = [{"name": legacy_name}]

    priorities = {}
    for old_key, new_key in PRIORITY_KEYS.items():
        value = olive.pop(old_key, None)
        if isinstance(value, list) and value:
            priorities[new_key] = value
    if priorities:
        config["priorities"] = priorities

    instructions = {}
    for old_key, new_key in INSTRUCTION_KEYS.items():
        text = olive.pop(old_key, None)
        if not isinstance(text, str) or not text.strip():
            continue

        file_name = prompt_file_name(new_key, section)
        prompts[file_name] = text.strip() + "\n"
        instructions[new_key] = {"file": file_name}

    if instructions:
        config["instructions"] = instructions

    for key in DROPPED_KEYS:
        olive.pop(key, None)

    return config


def migrate(phrases_path: Path, config_path: Path) -> bool:
    """Rewrite both files. Returns False when nothing was written."""
    if config_path.exists():
        print(f"{config_path} already exists — refusing to overwrite it. Move it aside and run again.")
        return False

    try:
        with open(phrases_path, "r", encoding="utf-8") as f:
            phrases = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Cannot read {phrases_path}: {e}")
        return False

    prompts: dict[str, str] = {}
    config: dict = {}

    for section, body in phrases.items():
        if not isinstance(body, dict) or not isinstance(body.get("olive"), dict):
            continue

        olive = body["olive"]
        migrated = migrate_section(olive, section, prompts)

        if migrated:
            config[section] = migrated
            print(f"  {section}: moved {', '.join(sorted(migrated))}")

        if not olive:
            # Everything it held was configuration; an empty section left behind
            # would only invite someone to put configuration back into it.
            del body["olive"]

    if not config:
        print("Nothing to migrate: no LLM configuration found in the phrases file.")
        return False

    # The agent's own instruction never lived in phrases, but naming it here
    # keeps every prompt the bot uses visible in one place.
    config.setdefault("global", {}).setdefault("instructions", {})["schedule_agent"] = {"file": AGENT_PROMPT_FILE}

    written = write_prompts(config_path.parent, prompts)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    print(f"Wrote {config_path} ({len(config)} section(s), {written} prompt file(s))")

    backup = phrases_path.with_suffix(phrases_path.suffix + ".bak")
    shutil.copyfile(phrases_path, backup)
    with open(phrases_path, "w", encoding="utf-8") as f:
        json.dump(phrases, f, ensure_ascii=False, indent=4)
    print(f"Rewrote {phrases_path} (backup at {backup.name})")

    return True


def write_prompts(base_dir: Path, prompts: dict[str, str]) -> int:
    """Write each collected instruction to its own file, never over an existing one."""
    written = 0

    for file_name, text in prompts.items():
        path = base_dir / file_name
        if path.exists():
            print(f"  {file_name}: already exists, left untouched (config still points at it)")
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written += 1
        print(f"  {file_name}: {len(text)} character(s)")

    return written


if __name__ == "__main__":
    # Both files are resolved against `src/`, which is where the bot reads them
    # from -- it is started from there.
    target_phrases = Path(sys.argv[1]) if len(sys.argv) > 1 else src_root / "phrases.json"
    target_config = Path(sys.argv[2]) if len(sys.argv) > 2 else src_root / "llm_config.json"

    print(f"Migrating LLM configuration out of {target_phrases}")
    migrate(target_phrases, target_config)
