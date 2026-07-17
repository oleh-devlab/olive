import json
import re
from datetime import datetime
from pathlib import Path
import sys

# Add src to sys.path to import core
src_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(src_root))

from core.time_utils import tz


def migrate_context(input_path: Path, output_path: Path):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Regular expression for matching tags in the format:
    # [Weekday, DD.MM.YYYY HH:MM:SS][DisplayName][UserName]: "..."
    # Do not anchor to the beginning of the line because it may contain
    # a reply prefix such as "[This is a reply to ...]"
    tag_pattern = re.compile(
        r"\[.*?,\s*(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})\]"  # Group 1: Timestamp
        r"\[([^\]]*)\]"  # Group 2: Display Name
        r"\[([^\]]*)\]"  # Group 3: User Name
        r':\s*"(.*)"$'  # Group 4: Content
    )

    migrated_users = 0
    migrated_models = 0

    for guild_id, messages in data.items():
        last_timestamp = 0

        for msg in messages:
            # Skip messages that already contain the new fields
            # (in case the migration is run multiple times)
            if "timestamp_ms" in msg and "author_name" in msg:
                continue

            if msg.get("role") == "user":
                parts = msg.get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
                    match = tag_pattern.search(text)
                    if match:
                        time_str, display_name, user_name, content = match.groups()

                        try:
                            # Convert the timestamp string to POSIX milliseconds
                            dt = datetime.strptime(time_str, "%d.%m.%Y %H:%M:%S")
                            dt = dt.replace(tzinfo=tz)
                            ts_ms = int(dt.timestamp() * 1000)
                            last_timestamp = ts_ms
                        except Exception as e:
                            print(f"Failed to parse date {time_str}: {e}")
                            ts_ms = last_timestamp

                        msg["timestamp_ms"] = ts_ms
                        msg["author_display_name"] = display_name
                        msg["author_name"] = user_name
                        msg["author_id"] = 0
                        msg["message_id"] = 0
                        migrated_users += 1
                    else:
                        msg["timestamp_ms"] = last_timestamp
                        msg["author_display_name"] = ""
                        msg["author_name"] = ""
                        msg["author_id"] = 0
                        msg["message_id"] = 0

            elif msg.get("role") in ("model", "assistant"):
                msg["timestamp_ms"] = last_timestamp
                migrated_models += 1

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    print("Migration completed successfully.")
    print(f"Updated {migrated_users} user messages and {migrated_models} model messages.")
    print(f"New file saved to: {output_path}")


if __name__ == "__main__":
    project_root = src_root.parent
    input_file = project_root / "llm_context.json"
    output_file = project_root / "llm_context_migrated.json"

    if not input_file.exists():
        print(f"Input file not found: {input_file}")
    else:
        migrate_context(input_file, output_file)
