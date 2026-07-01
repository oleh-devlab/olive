from dataclasses import dataclass, field
import datetime
from typing import Optional

from core.time_utils import tz
from modules.automatic_timetable_py.src.data_structs import Task as BaseTask, TimeBlock as BaseTimeBlock, Routine as BaseRoutine


@dataclass
class Task(BaseTask):
    """
    Olive-specific Task model that extends the scheduling core's Task
    with an ID and description.
    """
    id: Optional[int] = None
    description: str = ""


# Alias TimeBlock and Routine to be used within the Olive domain
TimeBlock = BaseTimeBlock
Routine = BaseRoutine


@dataclass
class ScheduleItem:
    is_task: bool
    task_name: str
    dt_start: datetime.datetime
    dt_end: datetime.datetime
    session_index: str
    total_sessions: int
    algo_notes: str

    @property
    def duration_min(self) -> int:
        return int((self.dt_end - self.dt_start).total_seconds() // 60)

    @property
    def date(self) -> datetime.date:
        return self.dt_start.date()
