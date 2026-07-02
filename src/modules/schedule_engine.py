import asyncio
import datetime
from core.time_utils import tz
from modules.schedule_models import ScheduleItem
from modules.schedule_provider import ScheduleProvider
from modules.automatic_timetable_py.src.scheduler import Scheduler


def _solve_sync(client_ID: int) -> list[ScheduleItem]:
    provider = ScheduleProvider()
    tasks = provider.list_tasks(client_ID)
    time_blocks = provider.list_time_blocks(client_ID)
    routines = provider.list_routines(client_ID)

    if not tasks and not routines:
        return []

    scheduler = Scheduler(max_horizon_days=14, priority_threshold=10)
    for t in tasks:
        scheduler.add_task(t)
    for b in time_blocks:
        scheduler.add_time_block(b)
    for r in routines:
        scheduler.add_routine(r)

    # Pass the timezone-aware start time so that it matches the timezone-aware deadlines
    now_tz = datetime.datetime.now(tz).replace(second=0, microsecond=0)
    result = scheduler.solve(start_time=now_tz)

    items = []
    if result.is_successful:
        # We can map routines here in the future if we need them as ScheduleItems
        for st in result.scheduled_tasks:
            if st.chunks:
                for i, chunk in enumerate(st.chunks):
                    items.append(
                        ScheduleItem(
                            is_task=True,
                            task_name=st.task.name,
                            dt_start=chunk.start_time,
                            dt_end=chunk.end_time,
                            session_index=str(i + 1),
                            total_sessions=len(st.chunks),
                            algo_notes="",
                        )
                    )
                    break_end = chunk.end_time + st.task.break_duration
                    if break_end > chunk.end_time:
                        items.append(
                            ScheduleItem(
                                is_task=False,
                                task_name="",
                                dt_start=chunk.end_time,
                                dt_end=break_end,
                                session_index="",
                                total_sessions=0,
                                algo_notes="Break",
                            )
                        )
            else:
                items.append(
                    ScheduleItem(
                        is_task=True,
                        task_name=st.task.name,
                        dt_start=st.start_time,
                        dt_end=st.end_time,
                        session_index="1",
                        total_sessions=1,
                        algo_notes="",
                    )
                )
                break_end = st.end_time + st.task.break_duration
                if break_end > st.end_time:
                    items.append(
                        ScheduleItem(
                            is_task=False,
                            task_name="",
                            dt_start=st.end_time,
                            dt_end=break_end,
                            session_index="",
                            total_sessions=0,
                            algo_notes="Break",
                        )
                    )

        for sr in result.scheduled_routines:
            r_note = "Fixed" if getattr(sr, "routine_type", "") == "fixed" else "Flexible"
            items.append(
                ScheduleItem(
                    is_task=True,
                    task_name=f"[Routine] {sr.task.name}",
                    dt_start=sr.start_time,
                    dt_end=sr.end_time,
                    session_index="1",
                    total_sessions=1,
                    algo_notes=r_note,
                )
            )
            break_end = sr.end_time + sr.task.break_duration
            if break_end > sr.end_time:
                items.append(
                    ScheduleItem(
                        is_task=False,
                        task_name="",
                        dt_start=sr.end_time,
                        dt_end=break_end,
                        session_index="",
                        total_sessions=0,
                        algo_notes="Break",
                    )
                )

    # Sort the items sequentially so they appear in order
    items.sort(key=lambda x: x.dt_start)

    return items


async def get_raw_schedule_items(client_ID: int) -> list[ScheduleItem]:
    """
    Main entry point for the schedule engine.
    Fetches the schedule using the current active algorithm.
    Runs the CPU-intensive solve operation in a background thread.
    """
    return await asyncio.to_thread(_solve_sync, client_ID)
