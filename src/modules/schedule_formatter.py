import datetime

from modules.schedule_models import ScheduleItem
from modules.schedule_engine import get_raw_schedule_items


def _format_block(item: ScheduleItem) -> str:
    """Format a single schedule item into display lines."""
    lines = []

    if item.is_task:
        header = f"  > {item.task_name}"
        lines.append(header)

        time_str = (
            f"    {item.dt_start.strftime('%H:%M')} -> {item.dt_end.strftime('%H:%M')}  ({item.duration_min} min)"
        )
        if item.total_sessions > 1:
            time_str += f"  [s. {item.session_index}/{item.total_sessions}]"
        lines.append(time_str)
    else:
        note = item.algo_notes if item.algo_notes else "Break"
        lines.append(f"  - {note}")
        if item.duration_min > 0:
            lines.append(
                f"    {item.dt_start.strftime('%H:%M')} -> {item.dt_end.strftime('%H:%M')}  ({item.duration_min} min)"
            )

    if item.algo_notes and item.is_task:
        lines.append(f"    !!! {item.algo_notes}")

    return "\n".join(lines)


async def _get_parsed_schedule_days(client_ID: int) -> tuple[list[dict], float, int, list[int]]:
    items, solve_time, planning_days, skipped_ids = await get_raw_schedule_items(client_ID)
    if not items:
        return [], solve_time, planning_days, skipped_ids

    days_dict: dict[datetime.date, dict] = {}

    for item in items:
        date_obj = item.date

        if date_obj not in days_dict:
            days_dict[date_obj] = {
                "date_obj": date_obj,
                "date_str": item.dt_start.strftime("%d.%m.%Y"),
                "weekday": date_obj.strftime("%A"),
                "blocks": [],
            }

        base_block = _format_block(item)
        days_dict[date_obj]["blocks"].append(base_block)

        # Duplicate the task onto the next day if it crosses midnight
        end_date = item.dt_end.date()
        if end_date > date_obj and (item.dt_end.hour > 0 or item.dt_end.minute > 0):
            if end_date not in days_dict:
                days_dict[end_date] = {
                    "date_obj": end_date,
                    "date_str": item.dt_end.strftime("%d.%m.%Y"),
                    "weekday": end_date.strftime("%A"),
                    "blocks": [],
                }

            # Add a visual marker for the spillover block
            spillover_block = base_block.replace("  > ", "  > [From yesterday] ").replace(
                "  - ", "  - [From yesterday] "
            )
            days_dict[end_date]["blocks"].append(spillover_block)

    return sorted(days_dict.values(), key=lambda x: x["date_obj"]), solve_time, planning_days, skipped_ids


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    days, solve_time, planning_days, skipped_ids = await _get_parsed_schedule_days(client_ID)
    if not days:
        return "You don't have any tasks or routines yet. Use `/task add` or `/routine add_flexible` to add your first items.\n"

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day['date_str']} ({day['weekday']}) ===")
        flat_lines.extend(day["blocks"])
        flat_lines.append("")  # Empty line between days

    return "\n".join(flat_lines)


async def get_schedule_by_day(client_ID: int) -> tuple[list[dict], float, int, list[int]]:
    """Returns structured schedule data and metadata for the UI paginator."""
    return await _get_parsed_schedule_days(client_ID)
