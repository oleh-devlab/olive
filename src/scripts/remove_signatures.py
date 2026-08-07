import argparse
import json
import sys
from pathlib import Path


def remove_signatures(filepath):
    path = Path(filepath)
    if not path.exists():
        print(f"Error: File '{filepath}' not found.")
        sys.exit(1)
        
    print(f"Loading '{filepath}'...")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON from '{filepath}'.\n{e}")
        sys.exit(1)
        
    removed_count = 0
    
    # Context format: { "discord_id": [ { "role": "...", "interaction_step": {...} }, ... ] }
    for guild_id, messages in data.items():
        for msg in messages:
            step = msg.get("interaction_step")
            if isinstance(step, dict) and "signature" in step:
                del step["signature"]
                removed_count += 1
                
    if removed_count > 0:
        with open(path, "w", encoding="utf-8") as f:
            # Save using the same formatting as LLMContextManager (compact)
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"Success! Removed {removed_count} 'signature' fields from '{filepath}'.")
    else:
        print(f"No 'signature' fields found in '{filepath}'. File was not modified.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Removes the obsolete 'signature' field from an LLM context JSON file.")
    parser.add_argument("filename", help="Path to the JSON file (e.g. schedule_agent_context.json)")
    
    args = parser.parse_args()
    remove_signatures(args.filename)
