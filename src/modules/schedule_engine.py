import asyncio
import collections
import datetime
import time

from core.time_utils import tz
from modules.schedule_models import ScheduleItem
from modules.schedule_provider import ScheduleProvider
from modules.automatic_timetable_py.src.scheduler import Scheduler
import settings


def _solve_sync(client_ID: int) -> list[ScheduleItem]:
    provider = ScheduleProvider()
    tasks = provider.list_tasks(client_ID)
    time_blocks = provider.list_time_blocks(client_ID)
    routines = provider.list_routines(client_ID)

    if not tasks and not routines:
        return []

    planning_days = provider.get_planning_days(client_ID)
    priority_threshold = provider.get_priority_threshold(client_ID)
    compute_timeout = provider.get_compute_timeout(client_ID)
    step_minutes = provider.get_step_minutes(client_ID)
    scheduler = Scheduler(min_horizon_days=planning_days, priority_threshold=priority_threshold, step_minutes=step_minutes)
    for t in tasks:
        scheduler.add_task(t)
    for b in time_blocks:
        scheduler.add_time_block(b)
    for r in routines:
        scheduler.add_routine(r)

    # Pass the timezone-aware start time so that it matches the timezone-aware deadlines
    now_tz = datetime.datetime.now(tz).replace(second=0, microsecond=0)

    workers = getattr(settings, "schedule_compute_workers", 1)
    
    start_perf = time.perf_counter()
    result = scheduler.solve(start_time=now_tz, timeout_seconds=compute_timeout, num_search_workers=workers)
    solve_time = time.perf_counter() - start_perf

    if result.status == "UNKNOWN":
        raise TimeoutError(f"CP-SAT solver timed out after {solve_time:.2f}s. Perhaps the planning horizon ({planning_days} days) is too long, or you've set a deadline that's too far in the future.")

    items = []
    skipped_ids = []
    skipped_routines = []
    if result.is_successful:
        skipped_ids = [st.task.id for st in getattr(result, "skipped_tasks", [])]
        # Group skipped routines by original ID or name
        grouped_routines = collections.defaultdict(list)
        for sr in getattr(result, "skipped_routines", []):
            t = sr.task
            # Extract routine ID from `t.id` if it starts with 'r_', else fallback to stripping date from name
            r_id = str(t.id).split('_')[1] if t.id and str(t.id).startswith('r_') else t.name.rsplit(' (', 1)[0]
            
            if t.deadline:
                grouped_routines[r_id].append(t.deadline.strftime('%d.%m %H:%M'))
            else:
                grouped_routines[r_id].append("no deadline")
                
        threshold = getattr(settings, "schedule_skipped_routine_collapse_threshold", 3)
        for r_id, deadlines in grouped_routines.items():
            if len(deadlines) > threshold:
                skipped_routines.append(f"{r_id} (missed {len(deadlines)} times)")
            else:
                skipped_routines.append(f"{r_id}: {', '.join(deadlines)}")
                
        # We can map routines here in the future if we need them as ScheduleItems
        for st in result.scheduled_tasks:
            if st.chunks:
                for i, chunk in enumerate(st.chunks):
                    items.append(
                        ScheduleItem(
                            item_type="task",
                            task_name=st.task.name,
                            dt_start=chunk.start_time,
                            dt_end=chunk.end_time,
                            session_index=str(i + 1),
                            total_sessions=len(st.chunks),
                            algo_notes="",
                        )
                    )
            else:
                items.append(
                    ScheduleItem(
                        item_type="task",
                        task_name=st.task.name,
                        dt_start=st.start_time,
                        dt_end=st.end_time,
                        session_index="1",
                        total_sessions=1,
                        algo_notes="",
                    )
                )

        for sr in result.scheduled_routines:
            itype = "fixed_routine" if getattr(sr, "routine_type", "") == "fixed" else "flexible_routine"
            items.append(
                ScheduleItem(
                    item_type=itype,
                    task_name=sr.task.name,
                    dt_start=sr.start_time,
                    dt_end=sr.end_time,
                    session_index="1",
                    total_sessions=1,
                    algo_notes="",
                )
            )    # Sort the items sequentially so they appear in order
    items.sort(key=lambda x: x.dt_start)

    return items, solve_time, planning_days, skipped_ids, skipped_routines, result.status


async def get_raw_schedule_items(client_ID: int) -> tuple[list[ScheduleItem], float, int, list[int], list[str], str]:
    """
    Main entry point for the schedule engine.
    Fetches the schedule using the current active algorithm.
    Runs the CPU-intensive solve operation in a background thread.
    Returns: (items, solve_time_seconds, planning_days, skipped_ids, skipped_routines, status_text)
    """
    return await asyncio.to_thread(_solve_sync, client_ID)
