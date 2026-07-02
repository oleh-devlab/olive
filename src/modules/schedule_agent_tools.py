import datetime
import settings
import inspect
import functools

def log_tool(modifies_schedule=False):
    def decorator(func):
        is_async = inspect.iscoroutinefunction(func)
        sig = inspect.signature(func)
        
        def _log_call(self_obj, args, kwargs):
            bound_args = sig.bind(self_obj, *args, **kwargs)
            bound_args.apply_defaults()
            
            logged_kwargs = {}
            for k, v in bound_args.arguments.items():
                if k == "self":
                    continue
                if v != sig.parameters[k].default:
                    logged_kwargs[k] = v
                    
            args_str = ", ".join(f"{k}={v!r}" for k, v in logged_kwargs.items())
            self_obj.used_tools.append(f"`{func.__name__}({args_str})`")
            
            if modifies_schedule:
                self_obj.schedule_modified = True

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                _log_call(self, args, kwargs)
                return await func(self, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(self, *args, **kwargs):
                _log_call(self, args, kwargs)
                return func(self, *args, **kwargs)
            return sync_wrapper

    return decorator


from core.time_utils import tz
from modules.schedule_models import Task
from modules.schedule_provider import ScheduleProvider
import modules.schedule_formatter as auto_timetable
from modules.schedule_exceptions import ScheduleValidationError
from modules.schedule_validators import validate_task_creation_data, validate_task_update_data


class ScheduleAgentTools:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.provider = ScheduleProvider()
        self.used_tools = []
        self.schedule_modified = False

    @log_tool(modifies_schedule=False)
    async def get_current_schedule(self) -> str:
        """
        Fetches the current automatically generated schedule for the user (the placed timetable).
        Use this if the user explicitly asks to see their schedule or what they should do next.
        """
        try:
            full_schedule = await auto_timetable.get_schedule(self.user_id)
            lines = full_schedule.strip().split("\n")

            # Truncate to prevent context bloat if the schedule is massive
            MAX_SCHEDULE_LINES = 60
            if len(lines) > MAX_SCHEDULE_LINES:
                return (
                    "\n".join(lines[:MAX_SCHEDULE_LINES])
                    + f"\n\n... (Schedule truncated. Only showing the first {MAX_SCHEDULE_LINES} lines)"
                )
            return full_schedule
        except Exception as e:
            raise RuntimeError(f"Failed to get schedule: {e}")

    @log_tool(modifies_schedule=False)
    def list_tasks(self) -> str:
        """
        Returns a list of all current tasks and their IDs.
        Use this to find a task's ID before editing, removing, or spending time on it, or when the user asks what tasks they have.
        """
        tasks = self.provider.list_tasks(self.user_id)
        if not tasks:
            return "No tasks found."

        lines = []
        for t in tasks:
            dur_mins = int(t.duration.total_seconds() // 60)
            lines.append(f"[ID: {t.id}] {t.name} - {dur_mins} min (Priority: {t.priority})")
        return "\n".join(lines)

    @log_tool(modifies_schedule=False)
    def get_task_info(self, task_id: int) -> str:
        """
        Returns detailed information about a specific task, including its deadline, description, session duration, and break duration.
        Use this to check specific details like deadlines.
        """
        task = self.provider.get_task(self.user_id, task_id)
        if not task:
            return f"Task {task_id} not found."

        lines = [
            f"ID: {task.id}",
            f"Name: {task.name}",
            f"Description: {task.description if task.description.strip() else '(none)'}",
            f"Priority: {task.priority}",
            f"Total Duration: {int(task.duration.total_seconds() // 60)} min",
            f"Session: {int(task.max_chunk_duration.total_seconds() // 60) if task.max_chunk_duration else 'N/A'} min",
            f"Break: {int(task.break_duration.total_seconds() // 60)} min",
        ]

        if task.deadline:
            dl_str = task.deadline.strftime("%d.%m.%Y %H:%M")
            lines.append(f"Deadline: {dl_str}")
        else:
            lines.append("Deadline: none")

        if task.min_chunk_duration:
            lines.append(f"Min session shortening allowed: {int(task.min_chunk_duration.total_seconds() // 60)} min")

        return "\n".join(lines)

    @log_tool(modifies_schedule=False)
    def list_time_blocks(self) -> str:
        """
        Returns a list of all time blocks (fixed schedule events).
        """
        blocks = self.provider.list_time_blocks(self.user_id)
        if not blocks:
            return "No time blocks found."

        lines = []
        for i, b in enumerate(blocks):
            try:
                st = b.start.strftime("%H:%M") if hasattr(b.start, "strftime") else "???"
                et = b.end.strftime("%H:%M") if hasattr(b.end, "strftime") else "???"
                rep = "Daily" if getattr(b, "daily", False) else "One-time"
                lines.append(f"[{i + 1}] {st} - {et} ({rep})")
            except Exception:
                pass
        return "\n".join(lines) if lines else "No valid time blocks."

    @log_tool(modifies_schedule=True)
    def add_task(
        self,
        name: str,
        duration_min: int,
        description: str = "",
        priority: int = getattr(settings, "schedule_default_priority", 1),
        max_chunk_duration_min: int = getattr(settings, "schedule_default_max_chunk_min", 45),
        break_duration_min: int = getattr(settings, "schedule_default_break_min", 15),
        min_chunk_duration_min: int | None = None,
        deadline: str | None = None,
    ) -> str:
        """
        Adds a new task to the user's schedule.
        Args:
            name: The name of the task.
            duration_min: The total duration of the task in minutes. Must be > 0.
            description: Optional detailed description.
            priority: Priority of the task (>= 1).
            max_chunk_duration_min: Length of a single work session in minutes. Default 45.
            break_duration_min: Length of the break after a session in minutes. Default 15.
            min_chunk_duration_min: Minimum allowed shortened session in minutes. Set to 0 if not allowed.
            deadline: Deadline string in format 'DD.MM.YYYY HH:MM'. Empty string if no deadline.
        """
        try:
            new_task = validate_task_creation_data(
                name=name,
                duration_min=duration_min,
                description=description,
                priority=priority,
                max_chunk_duration_min=max_chunk_duration_min,
                break_duration_min=break_duration_min,
                min_chunk_duration_min=min_chunk_duration_min,
                deadline=deadline,
            )
        except ScheduleValidationError as e:
            raise ValueError(str(e))

        new_id = self.provider.add_task(self.user_id, new_task)
        return f"Task '{name}' added successfully with ID {new_id}."

    @log_tool(modifies_schedule=True)
    def remove_task(self, task_id: int) -> str:
        """
        Removes a task from the user's schedule by its ID.
        Args:
            task_id: The ID of the task to remove.
        """
        removed = self.provider.remove_task(self.user_id, task_id)
        if removed:
            return f"Task {task_id} removed successfully."
        else:
            raise ValueError(f"Task {task_id} not found.")

    @log_tool(modifies_schedule=True)
    def edit_task(
        self,
        task_id: int,
        name: str | None = None,
        duration_min: int | None = None,
        description: str | None = None,
        priority: int | None = None,
        max_chunk_duration_min: int | None = None,
        break_duration_min: int | None = None,
        min_chunk_duration_min: int | None = None,
        deadline: str | None = None,
    ) -> str:
        """
        Edits specific fields of an existing task.
        Only provide the fields you want to change; omit any parameter you want to keep unchanged.
        Args:
            task_id: The ID of the task to edit.
            name: New name.
            duration_min: New total duration.
            description: New description.
            priority: New priority.
            max_chunk_duration_min: New session duration.
            break_duration_min: New break duration.
            min_chunk_duration_min: New min session duration (0 to remove).
            deadline: New deadline 'DD.MM.YYYY HH:MM' ('none' to remove).
        """
        try:
            updates = validate_task_update_data(
                name=name,
                duration_min=duration_min,
                description=description,
                priority=priority,
                max_chunk_duration_min=max_chunk_duration_min,
                break_duration_min=break_duration_min,
                min_chunk_duration_min=min_chunk_duration_min,
                deadline=deadline,
            )
        except ScheduleValidationError as e:
            raise ValueError(str(e))

        success = self.provider.edit_task(self.user_id, task_id, **updates)
        if success:
            return f"Task {task_id} updated successfully."
        else:
            raise ValueError(f"Task {task_id} not found.")

    @log_tool(modifies_schedule=True)
    def spend_task_time(self, task_id: int, minutes: int) -> str:
        """
        Subtracts time from a task's total duration.
        Args:
            task_id: The ID of the task.
            minutes: Number of minutes spent working on the task.
        """
        if minutes <= 0:
            raise ValueError("minutes must be > 0.")

        is_completed, remaining = self.provider.spend_task_time(self.user_id, task_id, minutes)
        if is_completed:
            return f"Subtracted {minutes} min. Task fully completed and moved to history!"
        else:
            return f"Subtracted {minutes} min. Remaining duration: {remaining} min."
