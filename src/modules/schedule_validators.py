import datetime
import settings

from core.time_utils import tz
from modules.schedule_models import Task
from modules.schedule_exceptions import ScheduleValidationError


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\t", " ").replace("\n", " ").strip()


def parse_deadline(deadline_str: str | None) -> datetime.datetime | None:
    if not deadline_str or str(deadline_str).lower() == "none":
        return None
    try:
        dt = datetime.datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
        return dt.replace(tzinfo=tz)
    except ValueError:
        raise ScheduleValidationError("Invalid deadline format. Use 'DD.MM.YYYY HH:MM' or 'none'.")


def calculate_chunk_durations(
    duration_min: int,
    max_chunk_min: int | None,
    min_chunk_min: int | None
) -> tuple[datetime.timedelta | None, datetime.timedelta | None]:
    max_chunk = datetime.timedelta(minutes=max_chunk_min) if max_chunk_min and max_chunk_min > 0 else None

    if min_chunk_min is not None and min_chunk_min > 0:
        min_chunk = datetime.timedelta(minutes=min_chunk_min)
    elif min_chunk_min == 0:
        if max_chunk_min and max_chunk_min > 0:
            min_chunk = datetime.timedelta(minutes=min(15, max_chunk_min))
        else:
            min_chunk = None
    else:
        if max_chunk_min and max_chunk_min > 0 and duration_min > max_chunk_min:
            min_chunk = datetime.timedelta(minutes=min(15, max_chunk_min))
        else:
            min_chunk = None

    return max_chunk, min_chunk


def validate_task_creation_data(
    name: str,
    duration_min: int,
    description: str = "",
    priority: int = getattr(settings, "schedule_default_priority", 1),
    max_chunk_duration_min: int = getattr(settings, "schedule_default_max_chunk_min", 45),
    break_duration_min: int = getattr(settings, "schedule_default_break_min", 15),
    min_chunk_duration_min: int | None = None,
    deadline: str | None = None,
) -> Task:
    if duration_min <= 0:
        raise ScheduleValidationError("Task duration must be greater than 0.")

    deadline_dt = parse_deadline(deadline)
    max_chunk, min_chunk = calculate_chunk_durations(
        duration_min, max_chunk_duration_min, min_chunk_duration_min
    )

    return Task(
        id=0,
        name=clean_text(name),
        duration=datetime.timedelta(minutes=duration_min),
        description=clean_text(description),
        deadline=deadline_dt,
        priority=priority,
        max_chunk_duration=max_chunk,
        break_duration=datetime.timedelta(minutes=break_duration_min),
        min_chunk_duration=min_chunk,
    )


def validate_task_update_data(
    name: str | None = None,
    duration_min: int | None = None,
    description: str | None = None,
    priority: int | None = None,
    max_chunk_duration_min: int | None = None,
    break_duration_min: int | None = None,
    min_chunk_duration_min: int | None = None,
    deadline: str | None = None,
) -> dict:
    updates = {}

    if name:
        updates["name"] = clean_text(name)
        
    if duration_min is not None and duration_min > 0:
        updates["duration"] = datetime.timedelta(minutes=duration_min)
        
    if description is not None and description.strip():
        updates["description"] = clean_text(description)
        
    if priority is not None and priority > 0:
        updates["priority"] = priority
        
    if max_chunk_duration_min is not None and max_chunk_duration_min > 0:
        updates["max_chunk_duration"] = datetime.timedelta(minutes=max_chunk_duration_min)
        
    if break_duration_min is not None and break_duration_min >= 0:
        updates["break_duration"] = datetime.timedelta(minutes=break_duration_min)

    if min_chunk_duration_min is not None and min_chunk_duration_min >= 0:
        if min_chunk_duration_min > 0:
            updates["min_chunk_duration"] = datetime.timedelta(minutes=min_chunk_duration_min)
        else:
            if max_chunk_duration_min is not None and max_chunk_duration_min > 0:
                updates["min_chunk_duration"] = datetime.timedelta(minutes=min(15, max_chunk_duration_min))
            else:
                updates["min_chunk_duration"] = None

    if deadline is not None and str(deadline).strip() != "":
        updates["deadline"] = parse_deadline(deadline)

    return updates
