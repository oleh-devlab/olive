import collections
import datetime

from modules.schedule_engine import get_raw_schedule_items
from modules.schedule_timeline import column_widths, format_day_blocks


async def _get_parsed_schedule_days(client_ID: int) -> tuple[list[dict], float, int, list[int], list[str], str]:
    items, solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text = await get_raw_schedule_items(
        client_ID
    )
    if not items:
        return [], solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text

    # Measured over the whole schedule, not one day: a column that shifted when
    # the reader turned the page would be worse than no column at all.
    columns = column_widths(items)

    items_by_day = collections.defaultdict(lambda: {"items": [], "spillovers": []})

    for item in items:
        date_obj = item.date
        items_by_day[date_obj]["items"].append(item)

        # Duplicate the task onto the next day if it crosses midnight
        end_date = item.dt_end.date()
        if end_date > date_obj and (item.dt_end.hour > 0 or item.dt_end.minute > 0):
            items_by_day[end_date]["spillovers"].append(item)

    days_dict: dict[datetime.date, dict] = {}
    for date_obj, data in items_by_day.items():
        days_dict[date_obj] = {
            "date_obj": date_obj,
            "date_str": date_obj.strftime("%d.%m.%Y"),
            "weekday": date_obj.strftime("%A"),
            "blocks": format_day_blocks(data["items"], data["spillovers"], columns),
            "routine_ids": {
                item.item_id
                for item in data["items"] + data["spillovers"]
                if item.item_id is not None and "routine" in item.item_type
            },
        }

    return (
        sorted(days_dict.values(), key=lambda x: x["date_obj"]),
        solve_time,
        planning_days,
        skipped_tasks_ids,
        skipped_routines,
        status_text,
    )


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    days, _solve_time, _planning_days, _skipped_tasks_ids, _skipped_routines, _status_text = (
        await _get_parsed_schedule_days(client_ID)
    )
    if not days:
        return "You don't have any tasks or routines yet. Use `/task add` or `/routine add_flexible` to add your first items.\n"

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day['date_str']} ({day['weekday']}) ===")
        flat_lines.extend(day["blocks"])
        flat_lines.append("")  # Empty line between days

    return "\n".join(flat_lines)


async def get_schedule_by_day(client_ID: int) -> tuple[list[dict], float, int, list[int], list[str], str]:
    """Returns structured schedule data and metadata for the UI paginator.

    Blocks stay chronological here. The paginator cuts a long day along that
    order and only then flips each page for the bottom-up view, so the first
    page of a day is its morning.
    """
    return await _get_parsed_schedule_days(client_ID)
