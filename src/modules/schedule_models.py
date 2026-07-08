from dataclasses import dataclass
import datetime
from typing import Optional

from modules.automatic_timetable_py.src.data_structs import (
    Task as BaseTask,
    TimeBlock as BaseTimeBlock,
    Routine as BaseRoutine,
)


@dataclass
class Task(BaseTask):
    """
    Olive-specific Task model that extends the scheduling core's Task
    with an ID and description.
    """

    id: Optional[int] = None
    description: str = ""


# Alias
TimeBlock = BaseTimeBlock
Routine = BaseRoutine


@dataclass
class ScheduleItem:
    item_type: str  # "task", "fixed_routine", "flexible_routine"

    @property
    def is_task(self) -> bool:
        return self.item_type in ("task", "fixed_routine", "flexible_routine")

    @property
    def tag(self) -> str:
        if self.item_type == "fixed_routine":
            return "[Fxd Rt.] "
        elif self.item_type == "flexible_routine":
            return "[Flb Rt.] "
        return ""

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
