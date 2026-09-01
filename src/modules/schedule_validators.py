import datetime

import settings

from core.time_utils import tz
from modules.schedule_exceptions import ScheduleValidationError
from modules.schedule_models import (
    REPEAT_DAILY,
    REPEAT_ONCE,
    REPEAT_WEEKLY,
    TIMEBLOCK_REPEATS,
    Routine,
    Task,
    TimeBlock,
)
from modules.schedule_provider import ScheduleProvider


def validate_weekdays(weekdays: list[int] | None, what: str) -> list[int]:
    """The days a weekly item recurs on. Weekly means at least one, Monday is 0."""
    if not weekdays or not isinstance(weekdays, list):
        raise ScheduleValidationError(f"Weekly {what} require a list of weekdays (0-6).")

    for wd in weekdays:
        if not isinstance(wd, int) or wd < 0 or wd > 6:
            raise ScheduleValidationError("Weekdays must be integers from 0 (Monday) to 6 (Sunday).")

    return weekdays


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\t", " ").replace("\n", " ").strip()


def parse_date(date_str: str | None) -> datetime.date | None:
    if not date_str or str(date_str).lower() == "none":
        return None
    try:
        return datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        raise ScheduleValidationError("Invalid date format. Use 'DD.MM.YYYY'.")


def parse_deadline(deadline_str: str | None) -> datetime.datetime | None:
    if not deadline_str or str(deadline_str).lower() == "none":
        return None
    try:
        dt = datetime.datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
        return dt.replace(tzinfo=tz)
    except ValueError:
        raise ScheduleValidationError("Invalid deadline format. Use 'DD.MM.YYYY HH:MM' or 'none'.")


def parse_depends_on(
    depends_on_str: str | None, user_id: int | None = None, self_id: int | None = None, item_type: str = "task"
) -> list[int]:
    if not depends_on_str:
        return []
    try:
        parsed_ids = [int(x.strip()) for x in depends_on_str.split(",") if x.strip().isdigit()]
    except ValueError:
        raise ScheduleValidationError("Invalid depends_on format. Use a comma-separated list of IDs (e.g. '1, 2, 5').")

    if self_id is not None and self_id in parsed_ids:
        raise ScheduleValidationError(f"An item cannot depend on itself (ID {self_id}).")

    if user_id is not None:
        provider = ScheduleProvider()
        if item_type == "task":
            items = provider.list_tasks(user_id)
        else:
            items = provider.list_routines(user_id)

        valid_ids = {t.id for t in items if getattr(t, "id", None) is not None}

        invalid_ids = [str(d) for d in parsed_ids if d not in valid_ids]
        if invalid_ids:
            raise ScheduleValidationError(f"The following dependency IDs do not exist: {', '.join(invalid_ids)}")

    return parsed_ids


def calculate_chunk_durations(
    duration_min: int, max_chunk_min: int | None, min_chunk_min: int | None
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
    depends_on: str | None = None,
    user_id: int | None = None,
) -> Task:
    if duration_min <= 0:
        raise ScheduleValidationError("Task duration must be greater than 0.")
    if priority < 0 or priority > 10:
        raise ScheduleValidationError("Priority must be between 0 and 10.")
    deadline_dt = parse_deadline(deadline)

    max_chunk, min_chunk = calculate_chunk_durations(duration_min, max_chunk_duration_min, min_chunk_duration_min)

    return Task(
        id=0,
        name=clean_text(name),
        duration=datetime.timedelta(minutes=duration_min),
        description=clean_text(description),
        depends_on=parse_depends_on(depends_on, user_id=user_id, item_type="task"),
        deadline=deadline_dt,
        priority=priority,
        max_chunk_duration=max_chunk,
        break_duration=datetime.timedelta(minutes=break_duration_min),
        min_chunk_duration=min_chunk,
    )


def parse_timeblock_bound(time_str: str, anchor: datetime.datetime | None = None) -> datetime.datetime:
    """One end of a block: a date and time spelled out, or a bare time of day.

    A bare 'HH:MM' needs a date to land on. On the way in that is today; on an
    edit it is the day the bound being replaced already sits on, so retiming a
    block does not drag one of its ends onto a different date than the other.
    """
    try:
        if " " in time_str:
            return datetime.datetime.strptime(time_str, "%d.%m.%Y %H:%M").replace(tzinfo=tz)

        h, m = map(int, time_str.split(":"))
        on = anchor if isinstance(anchor, datetime.datetime) else datetime.datetime.now(tz)
        return on.replace(hour=h, minute=m, second=0, microsecond=0)
    except ScheduleValidationError:
        raise
    except Exception:
        raise ScheduleValidationError("Invalid time format. Use 'HH:MM' or 'DD.MM.YYYY HH:MM'.")


def resolve_block_bounds(
    start_dt: datetime.datetime, end_dt: datetime.datetime
) -> tuple[datetime.datetime, datetime.datetime]:
    """A block's pair of bounds, with the one that crosses midnight moved onto the next day.

    Only when both landed on the same date, which is what a bare 'HH:MM' pair
    does -- once a date was stated, an end before the start is a mistake rather
    than a night shift.
    """
    if end_dt <= start_dt:
        if start_dt.date() == end_dt.date():
            end_dt += datetime.timedelta(days=1)
        else:
            raise ScheduleValidationError("End time must be after start time.")

    return start_dt, end_dt


def validate_timeblock_creation_data(
    start_time_str: str,
    end_time_str: str,
    repeat: str = REPEAT_ONCE,
    name: str = "",
    weekdays: list[int] | None = None,
) -> TimeBlock:
    # Checked before the parsing below, which reports everything it cannot read
    # as "invalid time format" -- which a bad weekday is not.
    if repeat not in TIMEBLOCK_REPEATS:
        raise ScheduleValidationError(f"Repeat must be one of {', '.join(TIMEBLOCK_REPEATS)}.")

    if repeat == REPEAT_WEEKLY:
        weekdays = validate_weekdays(weekdays, "time blocks")
    elif weekdays:
        raise ScheduleValidationError(f"Weekdays only apply to weekly time blocks, not '{repeat}' ones.")

    start_dt, end_dt = resolve_block_bounds(parse_timeblock_bound(start_time_str), parse_timeblock_bound(end_time_str))

    return TimeBlock(
        start=start_dt,
        end=end_dt,
        daily=(repeat == REPEAT_DAILY),
        name=name,
        weekdays=weekdays,
    )


def validate_timeblock_update_data(
    block: TimeBlock,
    start_time_str: str | None = None,
    end_time_str: str | None = None,
    repeat: str | None = None,
    name: str | None = None,
    weekdays: list[int] | None = None,
) -> dict:
    """The fields to change on an existing block, read against what it already says.

    Partial by design, which is why this takes the block and the creation
    validator does not: a block's bounds are a pair and its recurrence is spread
    over `daily` and `weekdays`, so moving one end -- or naming days -- only
    means something next to the half that is staying.
    """
    updates: dict = {}

    if name is not None:
        updates["name"] = clean_text(name)

    # Naming days is what makes a block weekly, the same way it does on the way
    # in; stating a repeat as well still wins, so "daily" can drop the days.
    if weekdays is not None and repeat is None:
        repeat = REPEAT_WEEKLY

    if repeat is not None:
        if repeat not in TIMEBLOCK_REPEATS:
            raise ScheduleValidationError(f"Repeat must be one of {', '.join(TIMEBLOCK_REPEATS)}.")

        if repeat == REPEAT_WEEKLY:
            # The days already on the block carry over, so a weekly block can be
            # renamed or moved without restating which days it falls on.
            days = weekdays if weekdays is not None else block.weekdays
            updates["weekdays"] = validate_weekdays(days, "time blocks")
        elif weekdays:
            raise ScheduleValidationError(f"Weekdays only apply to weekly time blocks, not '{repeat}' ones.")
        else:
            updates["weekdays"] = None

        updates["daily"] = repeat == REPEAT_DAILY

    if start_time_str is not None or end_time_str is not None:

        def kept(dt):
            # A bound carried over from a hand-written file may be naive, while
            # the one just parsed never is, and the two have to be comparable.
            if isinstance(dt, datetime.datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=tz)
            return dt

        was_start, was_end = kept(block.start), kept(block.end)
        start_dt = parse_timeblock_bound(start_time_str, was_start) if start_time_str is not None else was_start
        end_dt = parse_timeblock_bound(end_time_str, was_end) if end_time_str is not None else was_end
        updates["start"], updates["end"] = resolve_block_bounds(start_dt, end_dt)

    return updates


def validate_task_update_data(
    name: str | None = None,
    duration_min: int | None = None,
    description: str | None = None,
    priority: int | None = None,
    max_chunk_duration_min: int | None = None,
    break_duration_min: int | None = None,
    min_chunk_duration_min: int | None = None,
    deadline: str | None = None,
    depends_on: str | None = None,
    user_id: int | None = None,
    self_id: int | None = None,
) -> dict:
    updates = {}

    if name:
        updates["name"] = clean_text(name)

    if duration_min is not None and duration_min > 0:
        updates["duration"] = datetime.timedelta(minutes=duration_min)

    if description is not None and description.strip():
        updates["description"] = clean_text(description)

    if priority is not None:
        if priority < 0 or priority > 10:
            raise ScheduleValidationError("Priority must be between 0 and 10.")
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

    if depends_on is not None:
        updates["depends_on"] = parse_depends_on(depends_on, user_id=user_id, self_id=self_id, item_type="task")

    return updates


def parse_time(time_str: str | None) -> datetime.time | None:
    if not time_str or str(time_str).lower() == "none":
        return None
    try:
        dt = datetime.datetime.strptime(time_str, "%H:%M")
        return dt.time()
    except ValueError:
        raise ScheduleValidationError("Invalid time format. Use 'HH:MM'.")


def validate_routine_creation_data(
    name: str,
    routine_type: str,
    repeat: str,
    duration_min: int,
    time_str: str | None = None,
    deadline_time_str: str | None = None,
    weekdays: list[int] | None = None,
    priority: int = getattr(settings, "schedule_default_priority", 1),
    break_duration_min: int = getattr(settings, "schedule_default_break_min", 15),
    depends_on: str | None = None,
    user_id: int | None = None,
) -> Routine:
    if duration_min <= 0:
        raise ScheduleValidationError("Routine duration must be greater than 0.")

    if priority < 0 or priority > 10:
        raise ScheduleValidationError("Priority must be between 0 and 10.")

    if routine_type not in ("fixed", "flexible"):
        raise ScheduleValidationError("Routine type must be 'fixed' or 'flexible'.")

    if repeat not in ("daily", "weekly"):
        raise ScheduleValidationError("Repeat must be 'daily' or 'weekly'.")

    parsed_time = None
    if routine_type == "fixed":
        if not time_str:
            raise ScheduleValidationError("Fixed routines require a specific time.")
        parsed_time = parse_time(time_str)

    parsed_deadline = None
    if routine_type == "flexible":
        if not deadline_time_str:
            raise ScheduleValidationError("Flexible routines require a deadline time.")
        parsed_deadline = parse_time(deadline_time_str)

    if repeat == "weekly":
        validate_weekdays(weekdays, "routines")

    return Routine(
        name=clean_text(name),
        type=routine_type,
        repeat=repeat,
        duration=datetime.timedelta(minutes=duration_min),
        time=parsed_time,
        deadline_time=parsed_deadline,
        weekdays=weekdays,
        priority=priority,
        break_duration=datetime.timedelta(minutes=break_duration_min),
        depends_on=parse_depends_on(depends_on, user_id=user_id, item_type="routine"),
    )


def validate_routine_update_data(
    name: str | None = None,
    routine_type: str | None = None,
    repeat: str | None = None,
    duration_min: int | None = None,
    time_str: str | None = None,
    deadline_time_str: str | None = None,
    weekdays: list[int] | None = None,
    priority: int | None = None,
    break_duration_min: int | None = None,
    depends_on: str | None = None,
    user_id: int | None = None,
    self_id: int | None = None,
) -> dict:
    updates = {}

    if name:
        updates["name"] = clean_text(name)

    if routine_type:
        if routine_type not in ("fixed", "flexible"):
            raise ScheduleValidationError("Routine type must be 'fixed' or 'flexible'.")
        updates["type"] = routine_type

    if repeat:
        if repeat not in ("daily", "weekly"):
            raise ScheduleValidationError("Repeat must be 'daily' or 'weekly'.")
        updates["repeat"] = repeat

    if duration_min is not None and duration_min > 0:
        updates["duration"] = datetime.timedelta(minutes=duration_min)

    if time_str is not None:
        updates["time"] = parse_time(time_str)

    if deadline_time_str is not None:
        updates["deadline_time"] = parse_time(deadline_time_str)

    if weekdays is not None:
        if repeat == "weekly" or updates.get("repeat") == "weekly":
            validate_weekdays(weekdays, "routines")
        updates["weekdays"] = weekdays

    if priority is not None:
        if priority < 0 or priority > 10:
            raise ScheduleValidationError("Priority must be between 0 and 10.")
        updates["priority"] = priority

    if break_duration_min is not None and break_duration_min >= 0:
        updates["break_duration"] = datetime.timedelta(minutes=break_duration_min)

    if depends_on is not None:
        updates["depends_on"] = parse_depends_on(depends_on, user_id=user_id, self_id=self_id, item_type="routine")

    return updates


def validate_skip_routine_data(days: int | None = None, resume_after: str | None = None) -> datetime.date:
    """
    Validates skip parameters and returns the resume_after date.
    The routine will NOT be scheduled up to and including the resume_after date.
    """
    if days is not None and resume_after is not None:
        raise ScheduleValidationError("You cannot provide both 'days' and 'resume_after'. Choose one.")

    today = datetime.date.today()

    if resume_after is not None:
        parsed_date = parse_date(resume_after)
        if parsed_date < today:
            raise ScheduleValidationError("Cannot set resume_after to a past date.")
        # We just return the exact date provided by the user.
        return parsed_date

    if days is None:
        # Default to skipping just today
        days = 1

    if days <= 0:
        raise ScheduleValidationError("Days must be greater than 0.")

    # skip 1 day (today) -> resume_after = today
    # skip X days -> resume_after = today + (X - 1) days
    return today + datetime.timedelta(days=days - 1)
