import datetime

from core.time_utils import tz
from modules.schedule_models import ScheduleItem
from modules.schedule_engine import get_raw_schedule_items

_UK_WEEKDAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]


def _format_block(item: ScheduleItem) -> str:
    """Format a single schedule item into display lines."""
    lines = []

    if item.is_task:
        header = f"  > {item.task_name}"
        if item.total_sessions > 1:
            header += f"  [session {item.session_index}/{item.total_sessions}]"
        lines.append(header)
        lines.append(f"    {item.dt_start.strftime('%H:%M')} -> {item.dt_end.strftime('%H:%M')}  ({item.duration_min} min)")
    else:
        note = item.algo_notes if item.algo_notes else "Break"
        lines.append(f"  - {note}")
        if item.duration_min > 0:
            lines.append(f"    {item.dt_start.strftime('%H:%M')} -> {item.dt_end.strftime('%H:%M')}  ({item.duration_min} min)")

    if item.algo_notes and item.is_task:
        lines.append(f"    !!! {item.algo_notes}")

    return "\n".join(lines)


async def _get_parsed_schedule_days(client_ID: int) -> list[dict]:
    items = await get_raw_schedule_items(client_ID)
    if not items:
        return []

    days_dict: dict[datetime.date, dict] = {}

    for item in items:
        date_obj = item.date

        if date_obj not in days_dict:
            days_dict[date_obj] = {
                "date_obj": date_obj,
                "date_str": item.dt_start.strftime("%d.%m.%Y"),
                "weekday": _UK_WEEKDAYS[date_obj.weekday()],
                "blocks": []
            }

        days_dict[date_obj]["blocks"].append(_format_block(item))

    return sorted(days_dict.values(), key=lambda x: x["date_obj"])


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    days = await _get_parsed_schedule_days(client_ID)
    if not days:
        return "У вас ще немає завдань. Скористайтеся `/task add`, щоб додати перше завдання.\n"

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day['date_str']} ({day['weekday']}) ===")
        flat_lines.extend(day["blocks"])
        flat_lines.append("")  # Empty line between days

    return "\n".join(flat_lines)


async def get_schedule_by_day(client_ID: int) -> list[dict]:
    """Returns structured schedule data for the UI paginator."""
    return await _get_parsed_schedule_days(client_ID)
