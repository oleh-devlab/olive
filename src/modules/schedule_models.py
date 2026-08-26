import datetime
from dataclasses import dataclass

from modules.automatic_timetable_py.src.data_structs import (
    Routine as BaseRoutine,
)
from modules.automatic_timetable_py.src.data_structs import (
    Task as BaseTask,
)
from modules.automatic_timetable_py.src.data_structs import (
    TimeBlock as BaseTimeBlock,
)


@dataclass
class Task(BaseTask):
    """
    Olive-specific Task model that extends the scheduling core's Task
    with an ID and description.
    """

    id: int | None = None
    description: str = ""


# Alias
TimeBlock = BaseTimeBlock
Routine = BaseRoutine


@dataclass
class CompletedTask(Task):
    def __post_init__(self):
        # Disable validation for completed tasks (allow duration 0)
        pass


@dataclass
class ScheduleItem:
    item_type: str  # "task", "fixed_routine", "flexible_routine"

    @property
    def is_task(self) -> bool:
        return self.item_type in ("task", "fixed_routine", "flexible_routine")

    task_name: str
    dt_start: datetime.datetime
    dt_end: datetime.datetime
    session_index: str
    total_sessions: int
    algo_notes: str
    item_id: int | str | None = None

    @property
    def duration_min(self) -> int:
        return int((self.dt_end - self.dt_start).total_seconds() // 60)

    @property
    def date(self) -> datetime.date:
        return self.dt_start.date()
