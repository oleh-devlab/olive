import asyncio
import collections
import datetime
import time
from collections.abc import Iterator

import settings

from core.time_utils import tz
from modules.automatic_timetable_py.src.scheduler import Scheduler
from modules.schedule_models import ScheduleItem, SolvedSchedule
from modules.schedule_provider import ScheduleProvider
from modules import schedule_stats

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


def _load_scheduler(client_ID: int, provider: ScheduleProvider, planning_days: int) -> Scheduler | None:
    """A scheduler holding everything the user has, or None when there is nothing to solve."""
    tasks = provider.list_tasks(client_ID)
    routines = provider.list_routines(client_ID)

    if not tasks and not routines:
        return None

    scheduler = Scheduler(
        min_horizon_days=planning_days,
        priority_threshold=provider.get_priority_threshold(client_ID),
        step_minutes=provider.get_step_minutes(client_ID),
    )

    for task in tasks:
        scheduler.add_task(task)
    for time_block in provider.list_time_blocks(client_ID):
        scheduler.add_time_block(time_block)
    for routine in routines:
        scheduler.add_routine(routine)

    return scheduler


def _task_items(scheduled_tasks) -> Iterator[ScheduleItem]:
    """A task placed whole is one item; a task split into sessions is one per session."""
    for st in scheduled_tasks:
        # A whole task and a session both carry the times, so one loop covers both.
        sessions = st.chunks or [st]

        for index, session in enumerate(sessions, start=1):
            yield ScheduleItem(
                item_type="task",
                task_name=st.task.name,
                dt_start=session.start_time,
                dt_end=session.end_time,
                session_index=str(index),
                total_sessions=len(sessions),
                algo_notes="",
                item_id=st.task.id,
            )


def _routine_items(scheduled_routines) -> Iterator[ScheduleItem]:
    for sr in scheduled_routines:
        yield ScheduleItem(
            item_type="fixed_routine" if getattr(sr, "routine_type", "") == "fixed" else "flexible_routine",
            task_name=sr.task.name,
            dt_start=sr.start_time,
            dt_end=sr.end_time,
            session_index="1",
            total_sessions=1,
            algo_notes="",
            item_id=getattr(sr, "routine_id", None),
        )


def _time_block_items(scheduled_blocks) -> Iterator[ScheduleItem]:
    for tb in scheduled_blocks:
        yield ScheduleItem(
            item_type="time_block",
            task_name=tb.name,
            dt_start=tb.start_time,
            dt_end=tb.end_time,
            session_index="1",
            total_sessions=1,
            algo_notes="",
            item_id=getattr(tb, "id", None),
        )


def items_from_result(result) -> list[ScheduleItem]:
    """Everything the solver placed, as one chronological list."""
    items = [
        *_task_items(result.scheduled_tasks),
        *_routine_items(result.scheduled_routines),
        *_time_block_items(getattr(result, "scheduled_timeblocks", [])),
    ]
    items.sort(key=lambda item: item.dt_start)

    return items


def _solve_sync(client_ID: int) -> SolvedSchedule:
    provider = ScheduleProvider()
    planning_days = provider.get_planning_days(client_ID)

    scheduler = _load_scheduler(client_ID, provider, planning_days)
    if scheduler is None:
        return SolvedSchedule(planning_days=planning_days, status="NO_DATA")

    # Pass the timezone-aware start time so that it matches the timezone-aware deadlines
    now_tz = datetime.datetime.now(tz).replace(second=0, microsecond=0)
    workers = getattr(settings, "schedule_compute_workers", 1)

    start_perf = time.perf_counter()
    result = scheduler.solve(start_time=now_tz, timeouts=provider.get_timeouts(client_ID), num_search_workers=workers)
    solve_time = time.perf_counter() - start_perf

    if result.packer_status == "UNKNOWN":
        raise TimeoutError(
            f"CP-SAT solver timed out after {solve_time:.2f}s. Perhaps the planning horizon ({planning_days} days) is too long, or you've set a deadline that's too far in the future."
        )

    if not result.is_successful:
        return SolvedSchedule(solve_time=solve_time, planning_days=planning_days, status=result.status)

    return SolvedSchedule(
        items=items_from_result(result),
        solve_time=solve_time,
        planning_days=planning_days,
        skipped_task_ids=[st.task.id for st in getattr(result, "skipped_tasks", [])],
        skipped_routines=format_skipped_routines(getattr(result, "skipped_routines", [])),
        status=result.status,
    )


async def solve_schedule(client_ID: int) -> SolvedSchedule:
    """
    Main entry point for the schedule engine.
    Fetches the schedule using the current active algorithm.
    Runs the CPU-intensive solve operation in a background thread.
    """
    async with _solver_lock:
        result = await asyncio.to_thread(_solve_sync, client_ID)

    # Every solve passes through here — the loop's, the reader's and the
    # agent's alike — so this is the one place that has to count them.
    _items, solve_time, *_rest = result
    schedule_stats.record(client_ID, solve_time)

    return result
