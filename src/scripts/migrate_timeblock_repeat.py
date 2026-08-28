"""One-off rewrite of stored time blocks from the `daily` bool to the `repeat` word.

A time block used to say `"daily": true | false`, which had no way to spell the
third thing a block can now be -- weekly. The stored grammar therefore moved to
the one a routine already uses, `"repeat": "once" | "daily" | "weekly"`, with
`"weekdays"` alongside it when the block recurs on named days.

`ScheduleProvider` still reads the old key, so nothing breaks before this runs;
what it does is stop every file from carrying two vocabularies at once. Run it
once, from the repo root:

    python src/scripts/migrate_timeblock_repeat.py

It walks every `*_schedule.json` under `data/`, rewrites in place and is safe to
run twice -- a block already carrying `repeat` is left alone.
"""

import json
import sys
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(src_root))

REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"


def migrate_block(block: dict) -> bool:
    """Rewrite one block's recurrence. True when it changed."""
    if "repeat" in block:
        # Already migrated, or written by a version that never knew `daily`. A
        # bool left alongside the word is a second, contradictable answer, so it
        # goes -- and that counts as a change, or the caller would not write it.
        return block.pop("daily", None) is not None

    # Absent means daily, which is what the old reader defaulted to.
    block["repeat"] = REPEAT_DAILY if block.pop("daily", True) else REPEAT_ONCE
    return True


def migrate_file(path: Path) -> int:
    """Rewrite every time block in one user's schedule. Returns how many changed."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    blocks = data.get("time_blocks", [])
    changed = sum(migrate_block(b) for b in blocks if isinstance(b, dict))

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    return changed


def migrate_data_dir(data_dir: Path) -> tuple[int, int]:
    """Rewrite every schedule file in a data directory. Returns (files, blocks)."""
    files = 0
    blocks = 0

    for path in sorted(data_dir.glob("*_schedule.json")):
        try:
            changed = migrate_file(path)
        # One unreadable file should not stop the rest from being migrated.
        except Exception as e:
            print(f"  {path.name}: skipped ({e})")
            continue

        if changed:
            files += 1
            blocks += changed
            print(f"  {path.name}: {changed} time block(s) migrated")

    return files, blocks


if __name__ == "__main__":
    # The same directory the providers write to: repo-root `data/`.
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else src_root.parent / "data"

    if not target.is_dir():
        print(f"Data directory not found: {target}")
    else:
        print(f"Migrating time blocks in {target}")
        migrated_files, migrated_blocks = migrate_data_dir(target)
        print(f"Migration completed: {migrated_blocks} time block(s) across {migrated_files} file(s).")
