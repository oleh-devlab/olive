import asyncio
import collections
import datetime
import time

import settings

from core.time_utils import tz
from modules.automatic_timetable_py.src.scheduler import Scheduler
from modules.schedule_models import ScheduleItem
from modules.schedule_provider import ScheduleProvider

_solver_lock = asyncio.Lock()


def format_skipped_routines(skipped) -> list[str]:
    """Name each skipped routine once, with the days it was missed on.

    A routine runs at most once a day — it carries a single `time` or
    `deadline_time` — so the day alone identifies a missed occurrence, and the
    clock time would be the same number repeated after every date.
    """
    days_by_routine = collections.defaultdict(list)
    names = {}

    for sr in skipped:
        t = sr.task
        # The expander tags every day's copy with the routine it came from, so
        # the days group without taking the `r_{id}_{date}` task id apart. A
        # routine with no id of its own is its own key, by name.
        key = getattr(t, "routine_id", None) or t.name
        names.setdefault(key, t.name)

        day = t.deadline.strftime("%d.%m") if t.deadline else "no deadline"

        # Two occurrences cannot share a day, but a day printed twice would look
        # like a bug to the reader either way.
        if day not in days_by_routine[key]:
            days_by_routine[key].append(day)

    threshold = getattr(settings, "schedule_skipped_routine_collapse_threshold", 3)

    return [
        f"{names[key]} (missed {len(days)} times)" if len(days) > threshold else f"{names[key]}: {', '.join(days)}"
        for key, days in days_by_routine.items()
    ]


def _solve_sync(client_ID: int) -> tuple[list[ScheduleItem], float, int, list[int], list[str], str]:
    provider = ScheduleProvider()
    tasks = provider.list_tasks(client_ID)
    time_blocks = provider.list_time_blocks(client_ID)
    routines = provider.list_routines(client_ID)

    if not tasks and not routines:
        planning_days = provider.get_planning_days(client_ID)
        return [], 0.0, planning_days, [], [], "NO_DATA"
    planning_days = provider.get_planning_days(client_ID)
    priority_threshold = provider.get_priority_threshold(client_ID)
    timeouts = provider.get_timeouts(client_ID)
    step_minutes = provider.get_step_minutes(client_ID)
    scheduler = Scheduler(
        min_horizon_days=planning_days, priority_threshold=priority_threshold, step_minutes=step_minutes
    )
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
    result = scheduler.solve(start_time=now_tz, timeouts=timeouts, num_search_workers=workers)
    solve_time = time.perf_counter() - start_perf

    if result.packer_status == "UNKNOWN":
        raise TimeoutError(
            f"CP-SAT solver timed out after {solve_time:.2f}s. Perhaps the planning horizon ({planning_days} days) is too long, or you've set a deadline that's too far in the future."
        )

    items = []
    skipped_ids = []
    skipped_routines = []
    if result.is_successful:
        skipped_ids = [st.task.id for st in getattr(result, "skipped_tasks", [])]
        skipped_routines = format_skipped_routines(getattr(result, "skipped_routines", []))

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
                            item_id=st.task.id,
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
                        item_id=st.task.id,
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
                    item_id=getattr(sr, "routine_id", None),
                )
            )

        items.extend(
            ScheduleItem(
                item_type="time_block",
                task_name=tb.name,
                dt_start=tb.start_time,
                dt_end=tb.end_time,
                session_index="1",
                total_sessions=1,
                algo_notes="",
                item_id=getattr(tb, "id", None),
            )
            for tb in getattr(result, "scheduled_timeblocks", [])
        )

    items.sort(key=lambda x: x.dt_start)

    return items, solve_time, planning_days, skipped_ids, skipped_routines, result.status


async def get_raw_schedule_items(client_ID: int) -> tuple[list[ScheduleItem], float, int, list[int], list[str], str]:
    """
    Main entry point for the schedule engine.
    Fetches the schedule using the current active algorithm.
    Runs the CPU-intensive solve operation in a background thread.
    Returns: (items, solve_time_seconds, planning_days, skipped_ids, skipped_routines, status_text)
    """
    async with _solver_lock:
        return await asyncio.to_thread(_solve_sync, client_ID)
