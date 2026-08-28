import datetime
from dataclasses import dataclass, field

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


# A time block recurs in one of three ways, and the two layers spell that
# differently: olive's JSON says `repeat`, one word, the way a routine already
# does; the solver's dataclass says `daily: bool` plus `weekdays: list[int]`,
# because everything below it counts in steps and never sees a calendar. The
# vocabulary and the translation live here, next to the alias, so nothing else
# has to know a block states its recurrence in two fields.
REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
TIMEBLOCK_REPEATS = (REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKLY)


def block_repeat(block) -> str:
    """Which of the three a `TimeBlock` is, read back off the pair of fields."""
    # `weekdays` wins on its own: TimeBlock.__post_init__ clears `daily` for a
    # weekly block, so the two can never both be set on a well-formed object.
    if getattr(block, "weekdays", None):
        return REPEAT_WEEKLY

    return REPEAT_DAILY if getattr(block, "daily", False) else REPEAT_ONCE


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


@dataclass(frozen=True, slots=True)
class SolvedSchedule:
    """One solve: what the engine placed, and what it could not fit.

    Every field has a default so a solve that never ran — no tasks at all, or a
    solver that gave up — is the same shape as one that did.
    """

    items: list[ScheduleItem] = field(default_factory=list)
    solve_time: float = 0.0
    planning_days: int = 0
    skipped_task_ids: list[int] = field(default_factory=list)
    skipped_routines: list[str] = field(default_factory=list)
    status: str = ""
