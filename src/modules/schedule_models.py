from dataclasses import dataclass
import datetime
from typing import Optional


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
