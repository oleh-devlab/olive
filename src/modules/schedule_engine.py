import asyncio
from pathlib import Path
import csv
import io

import settings
from modules.schedule_models import ScheduleItem


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


def _parse_tsv_timetable(tsv_content: str) -> list[ScheduleItem]:
    file_like_tsv = io.StringIO(tsv_content)
    tsv_reader = csv.DictReader(file_like_tsv, delimiter="\t")

    items = []
    for row in tsv_reader:
        items.append(ScheduleItem(
            is_task=int(row.get("is_task", "0")) != 0,
            task_name=str(row.get("task_name", "")).strip(),
            start_time=int(row.get("start_time", "0")),
            end_time=int(row.get("end_time", "0")),
            session_index=str(row.get("session_index", "")),
            total_sessions=int(row.get("total_sessions", "0")),
            algo_notes=str(row.get("algo_notes", "") or "").strip(),
        ))

    return items


async def get_raw_schedule_items(client_ID: int) -> list[ScheduleItem]:
    """
    Main entry point for the schedule engine.
    Fetches the schedule using the current active algorithm.
    """
    tasks_file = Path(__file__).resolve().parent.parent.parent / "data" / f"{client_ID}_tasks.tsv"
    if not tasks_file.exists():
        return []

    tsv_content = await _generate_tsv_timetable(client_ID)
    return _parse_tsv_timetable(tsv_content)
