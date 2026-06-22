import os
import csv
from pathlib import Path

# Columns for tasks.tsv
TASKS_HEADER = ["id", "name", "description", "has_deadline", "deadline", "priority", "total_dur", "session_dur", "break_dur", "has_min_session", "min_session"]
# Columns for time_blocks.tsv
TIME_BLOCKS_HEADER = ["is_repeatable", "is_every_day", "start_time", "end_time", "day_of_week"]

def get_data_dir() -> Path:
    base = Path(__file__).resolve().parent.parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base

def get_tasks_file(user_id: int) -> Path:
    return get_data_dir() / f"{user_id}_tasks.tsv"

def get_time_blocks_file(user_id: int) -> Path:
    return get_data_dir() / f"{user_id}_time_blocks.tsv"

def _ensure_file(filepath: Path, header: list):
    if not filepath.exists():
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(header)

def list_tasks(user_id: int) -> list[dict]:
    filepath = get_tasks_file(user_id)
    if not filepath.exists():
        return []
    
    tasks = []
    with open(filepath, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            tasks.append(row)
    return tasks

def add_task(user_id: int, name: str, total_dur: int, description: str = "", priority: int = 1, session_dur: int = 45, break_dur: int = 15, min_session: int = None, deadline: int = None) -> int:
    filepath = get_tasks_file(user_id)
    _ensure_file(filepath, TASKS_HEADER)
    
    tasks = list_tasks(user_id)
    max_id = 0
    for t in tasks:
        if t['id'].isdigit():
            max_id = max(max_id, int(t['id']))
    
    new_id = max_id + 1
    
    has_deadline = 1 if deadline else 0
    deadline_val = deadline if deadline else 0
    
    has_min_session = 1 if min_session else 0
    min_session_val = min_session if min_session else 0
    
    row = {
        "id": new_id,
        "name": name.replace('\t', ' ').replace('\n', ' ').strip() or " ",
        "description": description.replace('\t', ' ').replace('\n', ' ').strip() or " ",
        "has_deadline": has_deadline,
        "deadline": deadline_val,
        "priority": priority,
        "total_dur": total_dur,
        "session_dur": session_dur,
        "break_dur": break_dur,
        "has_min_session": has_min_session,
        "min_session": min_session_val
    }
    
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter='\t')
        writer.writerow(row)
        
    return new_id

def remove_task(user_id: int, task_id: int) -> bool:
    filepath = get_tasks_file(user_id)
    if not filepath.exists():
        return False
        
    tasks = list_tasks(user_id)
    new_tasks = [t for t in tasks if str(t['id']) != str(task_id)]
    
    if len(tasks) == len(new_tasks):
        return False
        
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter='\t')
        writer.writeheader()
        writer.writerows(new_tasks)
        
    return True

def list_time_blocks(user_id: int) -> list[dict]:
    filepath = get_time_blocks_file(user_id)
    if not filepath.exists():
        return []
        
    blocks = []
    with open(filepath, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            blocks.append(row)
    return blocks

def add_time_block(user_id: int, start_mins: int, end_mins: int, is_repeatable: int = 1, is_every_day: int = 1, day_of_week: int = 0):
    filepath = get_time_blocks_file(user_id)
    _ensure_file(filepath, TIME_BLOCKS_HEADER)
    
    row = {
        "is_repeatable": is_repeatable,
        "is_every_day": is_every_day,
        "start_time": start_mins,
        "end_time": end_mins,
        "day_of_week": day_of_week
    }
    
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TIME_BLOCKS_HEADER, delimiter='\t')
        writer.writerow(row)

def remove_time_block(user_id: int, index: int) -> bool:
    # 0-indexed index of block
    filepath = get_time_blocks_file(user_id)
    if not filepath.exists():
        return False
        
    blocks = list_time_blocks(user_id)
    if index < 0 or index >= len(blocks):
        return False
        
    blocks.pop(index)
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=TIME_BLOCKS_HEADER, delimiter='\t')
        writer.writeheader()
        writer.writerows(blocks)
        
    return True
