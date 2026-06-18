import asyncio
from pathlib import Path
import csv
import io
import datetime

import settings

async def _generate_tsv_timetable(client_ID: int) -> str:
    if not isinstance(client_ID, int):
        raise ValueError("client_ID must be an integer.")

    base_dir = Path(__file__).parent.resolve() / 'automatic_timetable' / 'build'
    app_path = base_dir / settings.paths['auto_schedule_executable']

    cmd = [str(app_path), '--get_schedule', '--tasks_file', f'../../../../data/{client_ID}_tasks.tsv', '--time_blocks_file', f'../../../../data/{client_ID}_time_blocks.tsv', '--completed_tasks_file', f'../../../../data/{client_ID}_completed_tasks.tsv']

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(base_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()

    if process.returncode == 0:
        content = str(stdout.decode('utf-8').strip())
    else:
        content = str(stderr.decode('utf-8').strip())
        raise RuntimeError(content)

    return content

def _parse_tsv_timetable(tsv_content: str) -> dict:
    file_like_tsv = io.StringIO(tsv_content)
    tsv_reader = csv.DictReader(file_like_tsv, delimiter='\t')
    dict = {}
    for row in tsv_reader:
        for key, item in row.items():
            if key not in dict:
                dict[key] = []

            if item is None:
                item = '' # TODO: fix

            dict[key].append(item)
    
    return dict

async def get_schedule(client_ID: int) -> str:
    tsv_content = await _generate_tsv_timetable(client_ID)
    data = _parse_tsv_timetable(tsv_content)

    # TODO: рефакторинг: перевести типи даних і спростити

    def fmt_datetime(ts_minutes_str): # Заглушка
        ts_seconds = int(ts_minutes_str) * 60
        dt = datetime.datetime.fromtimestamp(ts_seconds)
        return dt.strftime("%Y-%m-%d %H:%M")

    def duration_min(start_str, end_str):
        return int(end_str) - int(start_str)

    output_lines = []
    
    n = len(data['is_task'])

    for i in range(n - 1, -1, -1):
        is_task = int(data['is_task'][i]) != 0
        task_name = str(data['task_name'][i]).strip() if data['task_name'][i] else ""
        start_time = data['start_time'][i]
        end_time = data['end_time'][i]
        session_index = data['session_index'][i]
        total_sessions = int(data['total_sessions'][i])
        
        raw_algo_notes = data['algo_notes'][i]
        algo_notes = str(raw_algo_notes).strip() if raw_algo_notes is not None else ""

        dur = duration_min(start_time, end_time)

        if is_task:
            header = f"  > {task_name}"
            if total_sessions > 1:
                header += f"  [session {session_index}/{total_sessions}]"
            output_lines.append(header)
            
            output_lines.append(f"    {fmt_datetime(start_time)} -> {fmt_datetime(end_time)}  ({dur} min)")
        else:
            note = algo_notes if algo_notes else "Break"
            output_lines.append(f"  - {note}")
            
            if dur > 0:
                output_lines.append(f"    {fmt_datetime(start_time)} -> {fmt_datetime(end_time)}  ({dur} min)")

        if algo_notes and is_task:
            output_lines.append(f"    !!! {algo_notes}")

    return "\n".join(output_lines) + "\n"
