from dataclasses import dataclass
import datetime
from typing import Optional

from core.time_utils import tz


@dataclass
class ScheduleItem:
    is_task: bool
    task_name: str
    start_time: int        # minutes since epoch
    end_time: int          # minutes since epoch
    session_index: str
    total_sessions: int
    algo_notes: str

    @property
    def duration_min(self) -> int:
        return self.end_time - self.start_time

    @property
    def dt_start(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.start_time * 60, tz=tz)

    @property
    def dt_end(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.end_time * 60, tz=tz)

    @property
    def date(self) -> datetime.date:
        return self.dt_start.date()


@dataclass
class Task:
    id: int
    name: str
    total_dur: datetime.timedelta
    description: str = ""
    deadline: Optional[datetime.datetime] = None
    priority: int = 1
    session_dur: datetime.timedelta = datetime.timedelta(minutes=45)
    break_dur: datetime.timedelta = datetime.timedelta(minutes=15)
    min_session: Optional[datetime.timedelta] = None


@dataclass
class TimeBlock:
    start_time: datetime.datetime
    end_time: datetime.datetime
    is_repeatable: bool = True
    is_every_day: bool = True
    day_of_week: int = 0
