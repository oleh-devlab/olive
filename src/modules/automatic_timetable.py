import asyncio
from pathlib import Path
import csv
import io
import datetime

import settings
from core.time_utils import tz


async def _generate_tsv_timetable(client_ID: int) -> str:
    if not isinstance(client_ID, int):
        raise ValueError("client_ID must be an integer.")

    base_dir = Path(__file__).parent.resolve() / "automatic_timetable" / "build"
    app_path = base_dir / settings.paths["auto_schedule_executable"]

    cmd = [
        str(app_path),
        "--get_schedule",
        "--tasks_file",
        f"../../../../data/{client_ID}_tasks.tsv",
        "--time_blocks_file",
        f"../../../../data/{client_ID}_time_blocks.tsv",
        "--completed_tasks_file",
        f"../../../../data/{client_ID}_completed_tasks.tsv",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(base_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode == 0:
        content = str(stdout.decode("utf-8").strip())
    else:
        content = str(stderr.decode("utf-8").strip())
        raise RuntimeError(content)

    return content


def _parse_tsv_timetable(tsv_content: str) -> dict:
    file_like_tsv = io.StringIO(tsv_content)
    tsv_reader = csv.DictReader(file_like_tsv, delimiter="\t")
    dict = {}
    for row in tsv_reader:
        for key, item in row.items():
            if key not in dict:
                dict[key] = []

            if item is None:
                item = ""  # TODO: fix

            dict[key].append(item)

    return dict


async def _get_parsed_schedule_days(client_ID: int) -> list[dict]:
    tasks_file = Path(__file__).resolve().parent.parent.parent / "data" / f"{client_ID}_tasks.tsv"
    if not tasks_file.exists():
        return []

    tsv_content = await _generate_tsv_timetable(client_ID)
    data = _parse_tsv_timetable(tsv_content)

    def get_datetime(ts_minutes_str):
        ts_seconds = int(ts_minutes_str) * 60
        return datetime.datetime.fromtimestamp(ts_seconds, tz=tz)

    def duration_min(start_str, end_str):
        return int(end_str) - int(start_str)

    days_dict = {}
    n = len(data["is_task"])

    for i in range(n):
        is_task = int(data["is_task"][i]) != 0
        task_name = str(data["task_name"][i]).strip() if data["task_name"][i] else ""
        start_time = data["start_time"][i]
        end_time = data["end_time"][i]
        session_index = data["session_index"][i]
        total_sessions = int(data["total_sessions"][i])

        raw_algo_notes = data["algo_notes"][i]
        algo_notes = str(raw_algo_notes).strip() if raw_algo_notes is not None else ""

        dur = duration_min(start_time, end_time)
        dt_start = get_datetime(start_time)
        dt_end = get_datetime(end_time)

        date_obj = dt_start.date()
        date_str = dt_start.strftime("%d.%m.%Y")

        uk_weekdays = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]
        weekday = uk_weekdays[date_obj.weekday()]

        if date_obj not in days_dict:
            days_dict[date_obj] = {
                "date_obj": date_obj,
                "date_str": date_str,
                "weekday": weekday,
                "blocks": []
            }

        block_lines = []
        if is_task:
            header = f"  > {task_name}"
            if total_sessions > 1:
                header += f"  [session {session_index}/{total_sessions}]"
            block_lines.append(header)
            block_lines.append(f"    {dt_start.strftime('%H:%M')} -> {dt_end.strftime('%H:%M')}  ({dur} min)")
        else:
            note = algo_notes if algo_notes else "Break"
            block_lines.append(f"  - {note}")
            if dur > 0:
                block_lines.append(f"    {dt_start.strftime('%H:%M')} -> {dt_end.strftime('%H:%M')}  ({dur} min)")

        if algo_notes and is_task:
            block_lines.append(f"    !!! {algo_notes}")

        days_dict[date_obj]["blocks"].append("\n".join(block_lines))

    sorted_days = sorted(days_dict.values(), key=lambda x: x["date_obj"])
    return sorted_days


async def get_schedule(client_ID: int) -> str:
    """Returns a full formatted schedule string for the agent."""
    days = await _get_parsed_schedule_days(client_ID)
    if not days:
        return "У вас ще немає завдань. Скористайтеся `/task add`, щоб додати перше завдання.\n"

    flat_lines = []
    for day in days:
        flat_lines.append(f"=== {day['date_str']} ({day['weekday']}) ===")
        # For the agent, forward chronological order is most logical
        flat_lines.extend(day["blocks"])
        flat_lines.append("") # Empty line between days

    return "\n".join(flat_lines)


async def get_schedule_by_day(client_ID: int) -> list[dict]:
    """Returns structured schedule data for the UI paginator."""
    return await _get_parsed_schedule_days(client_ID)
