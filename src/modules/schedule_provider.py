import json
import datetime
from pathlib import Path
from typing import List, Tuple, Optional

from modules.schedule_models import Task, TimeBlock, Routine


def get_data_dir() -> Path:
    base = Path(__file__).resolve().parent.parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_schedule_file(user_id: int) -> Path:
    return get_data_dir() / f"{user_id}_schedule.json"


def get_schedule_channels_file() -> Path:
    return get_data_dir() / "schedule_channels.json"


def _serialize_timedelta(td: Optional[datetime.timedelta]) -> Optional[int]:
    if td is None:
        return None
    return int(td.total_seconds() // 60)


def _deserialize_timedelta(minutes: Optional[int]) -> Optional[datetime.timedelta]:
    if minutes is None:
        return None
    return datetime.timedelta(minutes=minutes)


def _serialize_datetime(dt: Optional[datetime.datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _deserialize_datetime(dt_str: Optional[str]) -> Optional[datetime.datetime]:
    if not dt_str:
        return None
    return datetime.datetime.fromisoformat(dt_str)


def _serialize_time(t: Optional[datetime.time]) -> Optional[str]:
    if t is None:
        return None
    return t.strftime("%H:%M")


def _deserialize_time(t_str: Optional[str]) -> Optional[datetime.time]:
    if not t_str:
        return None
    try:
        return datetime.datetime.strptime(t_str, "%H:%M").time()
    except ValueError:
        return None


def _task_to_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "duration": _serialize_timedelta(task.duration),
        "deadline": _serialize_datetime(task.deadline),
        "priority": task.priority,
        "min_chunk_duration": _serialize_timedelta(task.min_chunk_duration),
        "max_chunk_duration": _serialize_timedelta(task.max_chunk_duration),
        "break_duration": _serialize_timedelta(task.break_duration),
    }


def _dict_to_task(d: dict) -> Task:
    return Task(
        id=d.get("id"),
        name=d.get("name", ""),
        description=d.get("description", ""),
        duration=_deserialize_timedelta(d.get("duration", 0)) or datetime.timedelta(minutes=0),
        deadline=_deserialize_datetime(d.get("deadline")),
        priority=d.get("priority", 0),
        min_chunk_duration=_deserialize_timedelta(d.get("min_chunk_duration")),
        max_chunk_duration=_deserialize_timedelta(d.get("max_chunk_duration")),
        break_duration=_deserialize_timedelta(d.get("break_duration", 0)) or datetime.timedelta(minutes=0),
    )


def _timeblock_to_dict(block: TimeBlock) -> dict:
    start_val = _serialize_datetime(block.start) if isinstance(block.start, datetime.datetime) else block.start
    end_val = _serialize_datetime(block.end) if isinstance(block.end, datetime.datetime) else block.end
    return {
        "start": start_val,
        "end": end_val,
        "daily": block.daily,
    }


def _dict_to_timeblock(d: dict) -> TimeBlock:
    start = d.get("start")
    if isinstance(start, str):
        start = _deserialize_datetime(start)
    end = d.get("end")
    if isinstance(end, str):
        end = _deserialize_datetime(end)
    return TimeBlock(
        start=start,
        end=end,
        daily=d.get("daily", True),
    )


def _routine_to_dict(routine: Routine) -> dict:
    return {
        "name": routine.name,
        "type": routine.type,
        "repeat": routine.repeat,
        "duration": _serialize_timedelta(routine.duration),
        "time": _serialize_time(routine.time) if isinstance(routine.time, datetime.time) else _serialize_datetime(routine.time),
        "deadline_time": _serialize_time(routine.deadline_time) if isinstance(routine.deadline_time, datetime.time) else _serialize_datetime(routine.deadline_time),
        "weekdays": routine.weekdays,
        "priority": routine.priority,
        "break_duration": _serialize_timedelta(routine.break_duration),
    }


def _dict_to_routine(d: dict) -> Routine:
    # Handle time/datetime parsing for time and deadline_time
    r_time = d.get("time")
    if isinstance(r_time, str):
        if "T" in r_time:
            r_time = _deserialize_datetime(r_time)
        else:
            r_time = _deserialize_time(r_time)

    r_deadline = d.get("deadline_time")
    if isinstance(r_deadline, str):
        if "T" in r_deadline:
            r_deadline = _deserialize_datetime(r_deadline)
        else:
            r_deadline = _deserialize_time(r_deadline)

    return Routine(
        name=d.get("name", ""),
        type=d.get("type", "fixed"),
        repeat=d.get("repeat", "daily"),
        duration=_deserialize_timedelta(d.get("duration", 0)) or datetime.timedelta(minutes=0),
        time=r_time,
        deadline_time=r_deadline,
        weekdays=d.get("weekdays"),
        priority=d.get("priority", 0),
        break_duration=_deserialize_timedelta(d.get("break_duration", 0)) or datetime.timedelta(minutes=0),
    )


class ScheduleProvider:

    def _load_data(self, user_id: int) -> dict:
        filepath = get_schedule_file(user_id)
        if not filepath.exists():
            return {"tasks": [], "time_blocks": [], "routines": [], "completed_tasks": []}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"tasks": [], "time_blocks": [], "routines": [], "completed_tasks": []}

    def _save_data(self, user_id: int, data: dict):
        filepath = get_schedule_file(user_id)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

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
        return self._load_data(user_id)

    def restore_backup(self, user_id: int, backup: dict):
        self._save_data(user_id, backup)

    def _validate_task(self, task: Task):
        if task.duration <= datetime.timedelta(minutes=0):
            raise ValueError("duration must be > 0.")
        if task.max_chunk_duration is not None and task.max_chunk_duration > task.duration:
            raise ValueError("max_chunk_duration must be <= duration.")
        if task.break_duration < datetime.timedelta(minutes=0):
            raise ValueError("break_duration must be >= 0.")
        if task.priority < 1:
            raise ValueError("priority must be >= 1.")

        if task.min_chunk_duration is not None and task.max_chunk_duration is not None:
            if task.min_chunk_duration <= datetime.timedelta(minutes=0) or task.min_chunk_duration > task.max_chunk_duration:
                raise ValueError("min_chunk_duration must be > 0 and <= max_chunk_duration.")

    def add_task(self, user_id: int, task: Task) -> int:
        self._validate_task(task)
        data = self._load_data(user_id)
        
        max_id = 0
        for t in data["tasks"] + data["completed_tasks"]:
            if t.get("id", 0) > max_id:
                max_id = t["id"]

        task.id = max_id + 1
        data["tasks"].append(_task_to_dict(task))
        self._save_data(user_id, data)
        return task.id

    def remove_task(self, user_id: int, task_id: int) -> bool:
        data = self._load_data(user_id)
        initial_len = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if t.get("id") != task_id]
        
        if len(data["tasks"]) != initial_len:
            self._save_data(user_id, data)
            return True
        return False

    def get_task(self, user_id: int, task_id: int) -> Optional[Task]:
        data = self._load_data(user_id)
        for t in data["tasks"]:
            if t.get("id") == task_id:
                return _dict_to_task(t)
        return None

    def list_tasks(self, user_id: int) -> List[Task]:
        data = self._load_data(user_id)
        return [_dict_to_task(t) for t in data.get("tasks", [])]

    def list_completed_tasks(self, user_id: int) -> List[Task]:
        data = self._load_data(user_id)
        return [_dict_to_task(t) for t in data.get("completed_tasks", [])]

    def spend_task_time(self, user_id: int, task_id: int, minutes: int) -> Tuple[bool, int]:
        data = self._load_data(user_id)
        target_idx = -1
        for i, t in enumerate(data["tasks"]):
            if t.get("id") == task_id:
                target_idx = i
                break

        if target_idx == -1:
            raise ValueError("Task not found")

        task_dict = data["tasks"][target_idx]
        current_dur_min = task_dict.get("duration", 0)
        new_dur_min = current_dur_min - minutes

        if new_dur_min <= 0:
            task_dict["duration"] = 0
            data["completed_tasks"].append(task_dict)
            data["tasks"].pop(target_idx)
            is_completed = True
            remaining = 0
        else:
            task_dict["duration"] = new_dur_min
            is_completed = False
            remaining = new_dur_min

        self._save_data(user_id, data)
        return is_completed, remaining

    def edit_task(self, user_id: int, task_id: int, **kwargs) -> bool:
        task = self.get_task(user_id, task_id)
        if not task:
            return False

        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)

        self._validate_task(task)

        data = self._load_data(user_id)
        for i, t in enumerate(data["tasks"]):
            if t.get("id") == task_id:
                data["tasks"][i] = _task_to_dict(task)
                break

        self._save_data(user_id, data)
        return True

    def add_time_block(self, user_id: int, block: TimeBlock):
        data = self._load_data(user_id)
        data.setdefault("time_blocks", []).append(_timeblock_to_dict(block))
        self._save_data(user_id, data)

    def list_time_blocks(self, user_id: int) -> List[TimeBlock]:
        data = self._load_data(user_id)
        return [_dict_to_timeblock(b) for b in data.get("time_blocks", [])]

    def remove_time_block(self, user_id: int, index: int) -> bool:
        data = self._load_data(user_id)
        blocks = data.get("time_blocks", [])
        if index < 0 or index >= len(blocks):
            return False

        blocks.pop(index)
        self._save_data(user_id, data)
        return True

    def add_routine(self, user_id: int, routine: Routine):
        data = self._load_data(user_id)
        data.setdefault("routines", []).append(_routine_to_dict(routine))
        self._save_data(user_id, data)

    def list_routines(self, user_id: int) -> List[Routine]:
        data = self._load_data(user_id)
        return [_dict_to_routine(r) for r in data.get("routines", [])]

    def remove_routine(self, user_id: int, index: int) -> bool:
        data = self._load_data(user_id)
        routines = data.get("routines", [])
        if index < 0 or index >= len(routines):
            return False

        routines.pop(index)
        self._save_data(user_id, data)
        return True
