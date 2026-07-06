import datetime
import collections

from modules.schedule_models import ScheduleItem
from modules.schedule_engine import get_raw_schedule_items


def _format_day_blocks(items: list[ScheduleItem], spillovers: list[ScheduleItem] = None) -> list[str]:
    blocks = []
    
    all_items = (spillovers or []) + items
    
    last_end = None
    
    for item in all_items:
        is_spill = item in (spillovers or [])
        lines = []
        
        # Start time at the top (with gap handling)
        if last_end and last_end == item.dt_start:
            pass # Shared boundary, skip printing the start time
        else:
            if last_end and item.dt_start > last_end:
                gap_mins = int((item.dt_start - last_end).total_seconds() / 60)
                lines.append(f" │    [ {gap_mins}m break ]")
            lines.append(item.dt_start.strftime("%H:%M"))
            
        if item.is_task:
            prefix = " ├──> "
            if is_spill:
                prefix += "[From yesterday] "
                
            task_line = f"{prefix}{item.tag}{item.task_name} ({item.duration_min}m)"
            if item.total_sessions > 1:
                task_line += f" [s. {item.session_index}/{item.total_sessions}]"
                
            if item.algo_notes:
                lines.append(f" │      !!! {item.algo_notes}")
            lines.append(task_line)
        else:
            note = item.algo_notes if item.algo_notes else "Break"
            prefix = " ├──- "
            if is_spill:
                prefix += "[From yesterday] "
            lines.append(f"{prefix}{note} ({item.duration_min}m)")

        # End time at the bottom
        lines.append(item.dt_end.strftime("%H:%M"))

        blocks.append("\n".join(lines))
        last_end = item.dt_end
        
    return blocks

async def _get_parsed_schedule_days(client_ID: int) -> tuple[list[dict], float, int, list[int], list[str], str]:
    items, solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text = await get_raw_schedule_items(client_ID)
    if not items:
        return [], solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text

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
            "blocks": _format_day_blocks(data["items"], data["spillovers"]),
        }

    return sorted(days_dict.values(), key=lambda x: x["date_obj"]), solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    days, solve_time, planning_days, skipped_tasks_ids, skipped_routines, status_text = await _get_parsed_schedule_days(client_ID)
    if not days:
        return "You don't have any tasks or routines yet. Use `/task add` or `/routine add_flexible` to add your first items.\n"

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day['date_str']} ({day['weekday']}) ===")
        flat_lines.extend(day["blocks"])
        flat_lines.append("")  # Empty line between days

    return "\n".join(flat_lines)


async def get_schedule_by_day(client_ID: int) -> tuple[list[dict], float, int, list[int], list[str], str]:
    """Returns structured schedule data and metadata for the UI paginator."""
    return await _get_parsed_schedule_days(client_ID)
