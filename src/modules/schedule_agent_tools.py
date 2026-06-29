import datetime

from core.time_utils import tz
from modules.schedule_models import Task
from modules.schedule_provider import ScheduleProvider
import modules.automatic_timetable as auto_timetable


class ScheduleAgentTools:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.provider = ScheduleProvider()

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
            dur_mins = int(t.total_dur.total_seconds() // 60)
            lines.append(f"[ID: {t.id}] {t.name} - {dur_mins} min (Priority: {t.priority})")
        return "\n".join(lines)

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
                st = b.start_time.strftime("%H:%M")
                et = b.end_time.strftime("%H:%M")
                rep = "Repeatable" if b.is_repeatable else "One-time"
                lines.append(f"[{i + 1}] {st} - {et} ({rep})")
            except Exception:
                pass
        return "\n".join(lines) if lines else "No valid time blocks."

    def add_task(
        self,
        name: str,
        total_dur_min: int,
        description: str = "",
        priority: int = 1,
        session_dur_min: int = 45,
        break_dur_min: int = 15,
        min_session_min: int = 0,
        deadline: str = "",
    ) -> str:
        """
        Adds a new task to the user's schedule.
        Args:
            name: The name of the task.
            total_dur_min: The total duration of the task in minutes. Must be > 0.
            description: Optional detailed description.
            priority: Priority of the task (>= 1).
            session_dur_min: Length of a single work session in minutes. Default 45.
            break_dur_min: Length of the break after a session in minutes. Default 15.
            min_session_min: Minimum allowed shortened session in minutes. Set to 0 if not allowed.
            deadline: Deadline string in format 'DD.MM.YYYY HH:MM'. Empty string if no deadline.
        """
        deadline_dt = None
        if deadline:
            try:
                dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                deadline_dt = dt.replace(tzinfo=tz)
            except ValueError:
                raise ValueError("Invalid deadline format. Use 'DD.MM.YYYY HH:MM'")

        min_sess = datetime.timedelta(minutes=min_session_min) if min_session_min > 0 else None

        new_task = Task(
            id=0,
            name=name,
            total_dur=datetime.timedelta(minutes=total_dur_min),
            description=description,
            deadline=deadline_dt,
            priority=priority,
            session_dur=datetime.timedelta(minutes=session_dur_min),
            break_dur=datetime.timedelta(minutes=break_dur_min),
            min_session=min_sess,
        )

        new_id = self.provider.add_task(self.user_id, new_task)
        return f"Task '{name}' added successfully with ID {new_id}."

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

    def edit_task(
        self,
        task_id: int,
        name: str = "",
        total_dur_min: int = 0,
        description: str = "",
        priority: int = 0,
        session_dur_min: int = 0,
        break_dur_min: int = -1,
        min_session_min: int = -1,
        deadline: str = "",
    ) -> str:
        """
        Edits specific fields of an existing task.
        Only provide the fields you want to change.
        Args:
            task_id: The ID of the task to edit.
            name: New name (leave empty to keep unchanged).
            total_dur_min: New total duration (0 to keep unchanged).
            description: New description (leave empty to keep unchanged).
            priority: New priority (0 to keep unchanged).
            session_dur_min: New session duration (0 to keep unchanged).
            break_dur_min: New break duration (-1 to keep unchanged).
            min_session_min: New min session duration (-1 to keep unchanged, 0 to remove).
            deadline: New deadline 'DD.MM.YYYY HH:MM' ('none' to remove, empty to keep unchanged).
        """
        updates = {}

        if name:
            updates["name"] = name.replace("\t", " ").replace("\n", " ").strip()
        if total_dur_min > 0:
            updates["total_dur"] = datetime.timedelta(minutes=total_dur_min)
        if description:
            updates["description"] = description.replace("\t", " ").replace("\n", " ").strip()
        if priority > 0:
            updates["priority"] = priority
        if session_dur_min > 0:
            updates["session_dur"] = datetime.timedelta(minutes=session_dur_min)
        if break_dur_min >= 0:
            updates["break_dur"] = datetime.timedelta(minutes=break_dur_min)
        if min_session_min >= 0:
            if min_session_min == 0:
                updates["min_session"] = None
            else:
                updates["min_session"] = datetime.timedelta(minutes=min_session_min)

        if deadline:
            if deadline.lower() == "none":
                updates["deadline"] = None
            else:
                try:
                    dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                    updates["deadline"] = dt.replace(tzinfo=tz)
                except ValueError:
                    raise ValueError("Invalid deadline format. Use 'DD.MM.YYYY HH:MM' or 'none'.")

        success = self.provider.edit_task(self.user_id, task_id, **updates)
        if success:
            return f"Task {task_id} updated successfully."
        else:
            raise ValueError(f"Task {task_id} not found.")

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
