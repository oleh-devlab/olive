import csv
import datetime
import json
from pathlib import Path
from typing import List, Tuple, Optional

from core.time_utils import tz
from .schedule_models import Task, TimeBlock

# Columns for tasks.tsv
TASKS_HEADER = [
    "id",
    "name",
    "description",
    "has_deadline",
    "deadline",
    "priority",
    "total_dur",
    "session_dur",
    "break_dur",
    "has_min_session",
    "min_session",
]
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


def get_completed_tasks_file(user_id: int) -> Path:
    return get_data_dir() / f"{user_id}_completed_tasks.tsv"


def get_schedule_channels_file() -> Path:
    return get_data_dir() / "schedule_channels.json"


def _ensure_file(filepath: Path, header: list):
    if not filepath.exists():
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(header)


class ScheduleProvider:

    def load_channels(self) -> dict:
        filepath = get_schedule_channels_file()
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_channels(self, data: dict):
        filepath = get_schedule_channels_file()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def create_backup(self, user_id: int) -> dict:
        """Create an in-memory snapshot of the user's schedule files."""
        files = {
            "tasks": get_tasks_file(user_id),
            "time_blocks": get_time_blocks_file(user_id),
            "completed_tasks": get_completed_tasks_file(user_id)
        }
        backup = {}
        for key, filepath in files.items():
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as f:
                    backup[key] = f.read()
            else:
                backup[key] = None
        return backup

    def restore_backup(self, user_id: int, backup: dict):
        """Restore the user's schedule files from an in-memory snapshot."""
        files = {
            "tasks": get_tasks_file(user_id),
            "time_blocks": get_time_blocks_file(user_id),
            "completed_tasks": get_completed_tasks_file(user_id)
        }
        for key, filepath in files.items():
            content = backup.get(key)
            if content is None:
                if filepath.exists():
                    filepath.unlink()
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

    def _parse_task_row(self, row: dict) -> Task:
        has_deadline = int(row["has_deadline"])
        deadline_dt = None
        if has_deadline:
            deadline_dt = datetime.datetime.fromtimestamp(int(row["deadline"]) * 60, tz=tz)

        has_min_session = int(row["has_min_session"])
        min_session_td = None
        if has_min_session:
            min_session_td = datetime.timedelta(minutes=int(row["min_session"]))

        return Task(
            id=int(row["id"]),
            name=row["name"],
            total_dur=datetime.timedelta(minutes=int(row["total_dur"])),
            description=row["description"],
            deadline=deadline_dt,
            priority=int(row["priority"]),
            session_dur=datetime.timedelta(minutes=int(row["session_dur"])),
            break_dur=datetime.timedelta(minutes=int(row["break_dur"])),
            min_session=min_session_td,
        )

    def _task_to_row(self, task: Task) -> dict:
        has_deadline = 1 if task.deadline else 0
        deadline_val = int(task.deadline.timestamp() / 60) if task.deadline else 0

        has_min_session = 1 if task.min_session else 0
        min_session_val = int(task.min_session.total_seconds() // 60) if task.min_session else 0

        return {
            "id": task.id,
            "name": task.name.replace("\t", " ").replace("\n", " ").strip() or " ",
            "description": task.description.replace("\t", " ").replace("\n", " ").strip() or " ",
            "has_deadline": has_deadline,
            "deadline": deadline_val,
            "priority": task.priority,
            "total_dur": int(task.total_dur.total_seconds() // 60),
            "session_dur": int(task.session_dur.total_seconds() // 60),
            "break_dur": int(task.break_dur.total_seconds() // 60),
            "has_min_session": has_min_session,
            "min_session": min_session_val,
        }

    def _list_tasks_raw(self, filepath: Path) -> List[dict]:
        if not filepath.exists():
            return []
        tasks = []
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                tasks.append(row)
        return tasks

    def _validate_task(self, task: Task):
        total_dur_min = int(task.total_dur.total_seconds() // 60)
        session_dur_min = int(task.session_dur.total_seconds() // 60)
        break_dur_min = int(task.break_dur.total_seconds() // 60)

        if total_dur_min <= 0:
            raise ValueError("total_dur_min must be > 0.")
        if session_dur_min <= 0 or session_dur_min > total_dur_min:
            raise ValueError("session_dur_min must be > 0 and <= total_dur_min.")
        if break_dur_min < 0:
            raise ValueError("break_dur_min must be >= 0.")
        if task.priority < 1:
            raise ValueError("priority must be >= 1.")

        if task.min_session is not None:
            min_session_min = int(task.min_session.total_seconds() // 60)
            if min_session_min <= 0 or min_session_min > session_dur_min:
                raise ValueError("min_session_min must be > 0 and <= session_dur_min.")

    def add_task(self, user_id: int, task: Task) -> int:
        self._validate_task(task)
        filepath = get_tasks_file(user_id)
        _ensure_file(filepath, TASKS_HEADER)

        raw_tasks = self._list_tasks_raw(filepath)
        max_id = 0
        for t in raw_tasks:
            if t["id"].isdigit():
                max_id = max(max_id, int(t["id"]))

        task.id = max_id + 1
        row = self._task_to_row(task)

        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter="\t")
            writer.writerow(row)

        return task.id

    def remove_task(self, user_id: int, task_id: int) -> bool:
        filepath = get_tasks_file(user_id)
        if not filepath.exists():
            return False

        raw_tasks = self._list_tasks_raw(filepath)
        new_tasks = [t for t in raw_tasks if str(t["id"]) != str(task_id)]

        if len(raw_tasks) == len(new_tasks):
            return False

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter="\t")
            writer.writeheader()
            writer.writerows(new_tasks)

        return True

    def get_task(self, user_id: int, task_id: int) -> Optional[Task]:
        filepath = get_tasks_file(user_id)
        raw_tasks = self._list_tasks_raw(filepath)
        for t in raw_tasks:
            if str(t["id"]) == str(task_id):
                return self._parse_task_row(t)
        return None

    def list_tasks(self, user_id: int) -> List[Task]:
        filepath = get_tasks_file(user_id)
        raw_tasks = self._list_tasks_raw(filepath)
        return [self._parse_task_row(t) for t in raw_tasks]

    def list_completed_tasks(self, user_id: int) -> List[Task]:
        filepath = get_completed_tasks_file(user_id)
        raw_tasks = self._list_tasks_raw(filepath)
        return [self._parse_task_row(t) for t in raw_tasks]

    def spend_task_time(self, user_id: int, task_id: int, minutes: int) -> Tuple[bool, int]:
        filepath = get_tasks_file(user_id)
        if not filepath.exists():
            raise ValueError("No tasks file found")

        raw_tasks = self._list_tasks_raw(filepath)
        target_idx = -1
        for i, t in enumerate(raw_tasks):
            if str(t["id"]) == str(task_id):
                target_idx = i
                break

        if target_idx == -1:
            raise ValueError("Task not found")

        task_row = raw_tasks[target_idx]
        current_dur = int(task_row["total_dur"])
        new_dur = current_dur - minutes

        if new_dur <= 0:
            completed_file = get_completed_tasks_file(user_id)
            _ensure_file(completed_file, TASKS_HEADER)
            with open(completed_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter="\t")
                writer.writerow(task_row)

            raw_tasks.pop(target_idx)
            is_completed = True
            remaining = 0
        else:
            task_row["total_dur"] = new_dur
            is_completed = False
            remaining = new_dur

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter="\t")
            writer.writeheader()
            writer.writerows(raw_tasks)

        return is_completed, remaining

    def edit_task(self, user_id: int, task_id: int, **kwargs) -> bool:
        task = self.get_task(user_id, task_id)
        if not task:
            return False

        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)

        self._validate_task(task)

        filepath = get_tasks_file(user_id)
        raw_tasks = self._list_tasks_raw(filepath)
        for i, t in enumerate(raw_tasks):
            if str(t["id"]) == str(task_id):
                raw_tasks[i] = self._task_to_row(task)
                break

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TASKS_HEADER, delimiter="\t")
            writer.writeheader()
            writer.writerows(raw_tasks)

        return True

    def _parse_timeblock_row(self, row: dict) -> TimeBlock:
        start_dt = datetime.datetime.fromtimestamp(int(row["start_time"]) * 60, tz=tz)
        end_dt = datetime.datetime.fromtimestamp(int(row["end_time"]) * 60, tz=tz)

        return TimeBlock(
            start_time=start_dt,
            end_time=end_dt,
            is_repeatable=bool(int(row["is_repeatable"])),
            is_every_day=bool(int(row["is_every_day"])),
            day_of_week=int(row["day_of_week"]),
        )

    def _timeblock_to_row(self, block: TimeBlock) -> dict:
        return {
            "is_repeatable": 1 if block.is_repeatable else 0,
            "is_every_day": 1 if block.is_every_day else 0,
            "start_time": int(block.start_time.timestamp() / 60),
            "end_time": int(block.end_time.timestamp() / 60),
            "day_of_week": block.day_of_week,
        }

    def _list_timeblocks_raw(self, filepath: Path) -> List[dict]:
        if not filepath.exists():
            return []
        blocks = []
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                blocks.append(row)
        return blocks

    def add_time_block(self, user_id: int, block: TimeBlock):
        filepath = get_time_blocks_file(user_id)
        _ensure_file(filepath, TIME_BLOCKS_HEADER)

        row = self._timeblock_to_row(block)
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TIME_BLOCKS_HEADER, delimiter="\t")
            writer.writerow(row)

    def list_time_blocks(self, user_id: int) -> List[TimeBlock]:
        filepath = get_time_blocks_file(user_id)
        raw_blocks = self._list_timeblocks_raw(filepath)
        return [self._parse_timeblock_row(b) for b in raw_blocks]

    def remove_time_block(self, user_id: int, index: int) -> bool:
        filepath = get_time_blocks_file(user_id)
        if not filepath.exists():
            return False

        raw_blocks = self._list_timeblocks_raw(filepath)
        if index < 0 or index >= len(raw_blocks):
            return False

        raw_blocks.pop(index)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TIME_BLOCKS_HEADER, delimiter="\t")
            writer.writeheader()
            writer.writerows(raw_blocks)

        return True
