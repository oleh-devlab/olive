import settings
import inspect
import functools

from modules.schedule_provider import ScheduleProvider
import modules.schedule_formatter as auto_timetable
from modules.schedule_exceptions import ScheduleValidationError
import modules.schd_item_formatters as schd_item_formatters
from modules.schedule_validators import validate_task_creation_data, validate_task_update_data, validate_routine_creation_data, validate_routine_update_data

MAX_SCHEDULE_LINES = 60

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


class ScheduleAgentTools:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.provider = ScheduleProvider()
        self.used_tools = []
        self.schedule_modified = False

    @log_tool(modifies_schedule=False)
    async def get_current_schedule(self) -> str:
        """
        Retrieves the user's current automatically generated schedule (the assigned timetable).
        Use this if the user explicitly asks to see their schedule or what they should do next. Or use it if you want to check something.
        This method returns only the first few dozen lines of the schedule, not the entire schedule.
        The user can view the entire schedule separately from your tools without your help, if they wish.
        """
        try:
            full_schedule = await auto_timetable.get_schedule(self.user_id)
            lines = full_schedule.strip().split("\n")

            # Truncate to prevent context bloat if the schedule is massive
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

        return schd_item_formatters.format_task_list(tasks, use_markdown=False)

    @log_tool(modifies_schedule=False)
    def get_task_info(self, task_id: int) -> str:
        """
        Returns detailed information about a specific task, including its deadline, description, session duration, and break duration.
        Use this to check specific details like deadlines.
        """
        task = self.provider.get_task(self.user_id, task_id)
        if not task:
            return f"Task {task_id} not found."

        return schd_item_formatters.format_task_info(task, use_markdown=False)

    @log_tool(modifies_schedule=False)
    def list_time_blocks(self) -> str:
        """
        Returns a list of all time blocks (fixed schedule events).
        """
        blocks = self.provider.list_time_blocks(self.user_id)
        if not blocks:
            return "No time blocks found."

        return schd_item_formatters.format_timeblock_list(blocks, use_markdown=False)

    @log_tool(modifies_schedule=True)
    def add_time_block(
        self,
        start_time_str: str,
        end_time_str: str,
        daily: bool = False
    ) -> str:
        """
        Adds a strict time block (busy time) during which NO tasks can be scheduled.
        Args:
            start_time_str: "HH:MM" e.g., "12:00"
            end_time_str: "HH:MM" e.g., "13:00"
            daily: True if this block happens every day, False if it's a one-time block for today.
        """
        try:
            from modules.schedule_validators import validate_timeblock_creation_data
            block = validate_timeblock_creation_data(start_time_str, end_time_str, daily)
        except ScheduleValidationError as e:
            raise ValueError(str(e))
            
        self.provider.add_time_block(self.user_id, block)
        return f"Time block added: {start_time_str} - {end_time_str} (Daily: {daily})."

    @log_tool(modifies_schedule=True)
    def remove_time_block(self, index: int) -> str:
        """
        Removes a time block by its index (1-based, use list_time_blocks first).
        Args:
            index: The 1-based index of the time block to remove.
        """
        removed = self.provider.remove_time_block(self.user_id, index - 1)
        if removed:
            return f"Time block {index} removed successfully."
        else:
            raise ValueError(f"Time block {index} not found.")

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
        depends_on: str | None = None,
    ) -> str:
        """
        Adds a new task to the user's schedule.
        Args:
            name: The name of the task.
            duration_min: The total duration of the task in minutes. Must be > 0.
            description: Optional detailed description.
            priority: Priority of the task (0 to 10). 0 means floating (scheduled anywhere), 1-10 sorts by importance (scheduled earlier).
            max_chunk_duration_min: Length of a single work session in minutes. Default 45.
            break_duration_min: Length of the break after a session in minutes. Default 15.
            min_chunk_duration_min: Minimum allowed shortened session in minutes. Set to 0 if not allowed.
            deadline: Deadline string in format 'DD.MM.YYYY HH:MM'. Empty string if no deadline.
            depends_on: Optional. Comma-separated list of IDs of tasks or routines this task depends on (e.g., '1, 3').
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
                depends_on=depends_on,
                user_id=self.user_id,
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
        depends_on: str | None = None,
    ) -> str:
        """
        Edits specific fields of an existing task.
        Only provide the fields you want to change; omit any parameter you want to keep unchanged.
        Args:
            task_id: The ID of the task to edit.
            name: New name.
            duration_min: New total duration.
            description: New description.
            priority: New priority (0 to 10). 0 means floating, 1-10 sorts by importance.
            max_chunk_duration_min: New session duration.
            break_duration_min: New break duration.
            min_chunk_duration_min: New min session duration (0 to remove).
            deadline: New deadline 'DD.MM.YYYY HH:MM' ('none' to remove).
            depends_on: New dependencies (comma-separated list of IDs, or 'none' to remove).
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
                depends_on=depends_on,
                user_id=self.user_id,
                self_id=task_id,
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

    @log_tool(modifies_schedule=True)
    def add_routine(
        self,
        name: str,
        routine_type: str,
        repeat: str,
        duration_min: int,
        time_str: str | None = None,
        deadline_time_str: str | None = None,
        weekdays: list[int] | None = None,
        priority: int = getattr(settings, "schedule_default_priority", 1),
        break_duration_min: int = getattr(settings, "schedule_default_break_min", 15),
        depends_on: str | None = None,
    ) -> str:
        """
        Adds a new routine.
        Args:
            name: Routine name.
            routine_type: 'fixed' (starts at exact time) or 'flexible' (can be scheduled any time before deadline).
            repeat: 'daily' or 'weekly'.
            duration_min: Total duration of the routine in minutes.
            time_str: Required if routine_type='fixed'. 'HH:MM'.
            deadline_time_str: Required if routine_type='flexible'. 'HH:MM'.
            weekdays: Required if repeat='weekly'. List of integers (0=Mon, 6=Sun).
            priority: Priority (0 to 10). 0 means floating, 1-10 sorts by importance.
            break_duration_min: Break duration after the routine.
            depends_on: Optional. Comma-separated list of IDs of tasks or routines this routine depends on (e.g., '1, 3').
        """
        try:
            new_routine = validate_routine_creation_data(
                name=name,
                routine_type=routine_type,
                repeat=repeat,
                duration_min=duration_min,
                time_str=time_str,
                deadline_time_str=deadline_time_str,
                weekdays=weekdays,
                priority=priority,
                break_duration_min=break_duration_min,
                depends_on=depends_on,
                user_id=self.user_id,
            )
        except ScheduleValidationError as e:
            raise ValueError(str(e))
            
        self.provider.add_routine(self.user_id, new_routine)
        return f"Routine '{name}' added successfully."

    @log_tool(modifies_schedule=False)
    def list_routines(self) -> str:
        """
        Lists all routines.
        """
        routines = self.provider.list_routines(self.user_id)
        if not routines:
            return "No routines found."

        return schd_item_formatters.format_routine_list(routines, use_markdown=False)

    @log_tool(modifies_schedule=False)
    def get_routine_info(self, routine_id: int) -> str:
        """
        Returns detailed information about a specific routine, including its exact time or deadline, repeat type, duration, and priority.
        Args:
            routine_id: The ID of the routine (use list_routines first to find it).
        """
        routines = self.provider.list_routines(self.user_id)
        
        # Find routine by ID
        r = next((r for r in routines if r.id == routine_id), None)
        if not r:
            return f"Routine {routine_id} not found."
            
        return schd_item_formatters.format_routine_info(r, use_markdown=False)

    @log_tool(modifies_schedule=True)
    def remove_routine(self, routine_id: int) -> str:
        """
        Removes a routine by its ID.
        Args:
            routine_id: The ID of the routine.
        """
        removed = self.provider.remove_routine(self.user_id, routine_id)
        if removed:
            return f"Routine {routine_id} removed successfully."
        else:
            raise ValueError(f"Routine {routine_id} not found.")

    @log_tool(modifies_schedule=True)
    def skip_routine(self, routine_id: int, days: int | None = None, resume_after: str | None = None) -> str:
        """
        Skips a routine for today, for X days, or until a specific date.
        Args:
            routine_id: The ID of the routine.
            days: Optional. The number of days to skip (e.g., 1 to skip just today).
            resume_after: Optional. The date to skip until in 'DD.MM.YYYY' format. For example, resume_after = "05.07.2026" means that the routine will be skipped through and including 05.07.2026 and will resume only after 05.07.2026.
        """
        try:
            from modules.schedule_validators import validate_skip_routine_data
            resume_date = validate_skip_routine_data(days=days, resume_after=resume_after)
        except ScheduleValidationError as e:
            raise ValueError(str(e))
            
        success = self.provider.skip_routine(self.user_id, routine_id, resume_date)
        if success:
            return f"Routine {routine_id} will be skipped and will resume on {resume_date.strftime('%d.%m.%Y')}."
        else:
            raise ValueError(f"Routine {routine_id} not found.")

    @log_tool(modifies_schedule=True)
    def edit_routine(
        self,
        routine_id: int,
        name: str | None = None,
        routine_type: str | None = None,
        repeat: str | None = None,
        duration_min: int | None = None,
        time_str: str | None = None,
        deadline_time_str: str | None = None,
        weekdays: list[int] | None = None,
        priority: int | None = None,
        break_duration_min: int | None = None,
        depends_on: str | None = None,
    ) -> str:
        """
        Edits specific fields of an existing routine.
        Only provide the fields you want to change; omit any parameter you want to keep unchanged.
        Args:
            routine_id: The ID of the routine.
            name: New name.
            routine_type: 'fixed' or 'flexible'.
            repeat: 'daily' or 'weekly'.
            duration_min: New duration in minutes.
            time_str: New time 'HH:MM'.
            deadline_time_str: New deadline time 'HH:MM'.
            weekdays: New list of weekdays (0=Mon, 6=Sun).
            priority: New priority (0 to 10). 0 means floating, 1-10 sorts by importance.
            break_duration_min: New break duration.
            depends_on: New dependencies (comma-separated list of IDs, or 'none' to remove).
        """
        try:
            updates = validate_routine_update_data(
                name=name,
                routine_type=routine_type,
                repeat=repeat,
                duration_min=duration_min,
                time_str=time_str,
                deadline_time_str=deadline_time_str,
                weekdays=weekdays,
                priority=priority,
                break_duration_min=break_duration_min,
                depends_on=depends_on,
                user_id=self.user_id,
                self_id=routine_id,
            )
        except ScheduleValidationError as e:
            raise ValueError(str(e))
        
        success = self.provider.edit_routine(self.user_id, routine_id, **updates)
        if success:
            return f"Routine {routine_id} updated successfully."
        else:
            raise ValueError(f"Routine {routine_id} not found.")

