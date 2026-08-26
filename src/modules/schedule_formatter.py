"""The schedule as one flat block of text, for the agent to read.

The reader's own view is not built here: the cog asks the engine for a solve,
`schedule_timeline` for the days, and `schedule_pagination` for the pages.
"""

from modules.schedule_engine import solve_schedule
from modules.schedule_timeline import group_into_days

NO_ITEMS_TEXT = (
    "You don't have any tasks or routines yet. Use `/task add` or `/routine add_flexible` to add your first items.\n"
)


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    schedule = await solve_schedule(client_ID)
    days = group_into_days(schedule.items)

    if not days:
        return NO_ITEMS_TEXT

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day.date_str} ({day.weekday}) ===")
        flat_lines.extend(day.blocks)
        flat_lines.append("")  # Empty line between days

    return "\n".join(flat_lines)
