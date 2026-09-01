import logging

import disnake
import settings
from disnake.ext import commands

from core import cache, utils
from core.personal_channels import ChannelSetupError, create_channel_pair
from modules import schd_item_formatters
from modules.schedule_exceptions import ScheduleValidationError
from modules.schedule_models import REPEAT_ONCE, REPEAT_WEEKLY, TIMEBLOCK_REPEATS
from modules.schedule_provider import ScheduleProvider
from modules.schedule_validators import (
    validate_routine_creation_data,
    validate_routine_update_data,
    validate_skip_routine_data,
    validate_task_creation_data,
    validate_task_update_data,
    validate_timeblock_creation_data,
    validate_timeblock_update_data,
)

logger = logging.getLogger(__name__)

# We can instantiate the provider here.
provider = ScheduleProvider()
phrases_cmd = utils.get_phrases().get("schedule_cmd", {})


class AutoSchedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(test_guilds=settings.guilds, description=phrases_cmd.get("cmd_task_desc", "Manage tasks"))
    async def task(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @task.sub_command(name="add", description=phrases_cmd.get("cmd_task_add_desc", "Add a new task"))
    async def task_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(description=phrases_cmd.get("param_name", "Name")),
        duration_min: int = commands.Param(description=phrases_cmd.get("param_duration_min", "Duration (min)")),
        description: str = commands.Param(default="", description=phrases_cmd.get("param_description", "Description")),
        priority: int = commands.Param(
            default=getattr(settings, "schedule_default_priority", 1),
            description=phrases_cmd.get("param_priority", "Priority (0-10)"),
        ),
        max_chunk_duration_min: int = commands.Param(
            default=getattr(settings, "schedule_default_max_chunk_min", 45),
            description=phrases_cmd.get("param_max_chunk", "Max session (min)"),
        ),
        break_duration_min: int = commands.Param(
            default=getattr(settings, "schedule_default_break_min", 15),
            description=phrases_cmd.get("param_break", "Break between sessions (min)"),
        ),
        min_chunk_duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_min_chunk", "Min session (min)")
        ),
        deadline: str = commands.Param(
            default=None, description=phrases_cmd.get("param_deadline", "Deadline (DD.MM.YYYY HH:MM)")
        ),
        depends_on: str = commands.Param(
            default=None, description=phrases_cmd.get("param_depends", "Dependencies (comma-separated IDs)")
        ),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            tasks = provider.list_tasks(inter.author.id)
            max_tasks = getattr(settings, "schedule_max_tasks_per_user", 200)
            if len(tasks) >= max_tasks:
                return await inter.edit_original_response(f"You have reached the maximum limit of {max_tasks} tasks.")

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
                user_id=inter.author.id,
            )
            new_id = provider.add_task(inter.author.id, new_task)
            await inter.edit_original_response(f"Task '{name}' added successfully with ID {new_id}.")
        except ScheduleValidationError as e:
            await inter.edit_original_response(str(e))
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @task.sub_command(name="remove", description=phrases_cmd.get("cmd_task_remove_desc", "Remove a task by ID"))
    async def task_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int = commands.Param(description=phrases_cmd.get("param_task_id", "Task ID")),
    ):
        await inter.response.defer(ephemeral=True)
        removed = provider.remove_task(inter.author.id, task_id)
        if removed:
            await inter.edit_original_response(f"Task {task_id} removed successfully.")
        else:
            await inter.edit_original_response(f"Task {task_id} not found.")

    @task.sub_command(name="list", description=phrases_cmd.get("cmd_task_list_desc", "List all current tasks"))
    async def task_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = provider.list_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No tasks found.")
            return

        formatted = schd_item_formatters.format_task_list(tasks, schd_item_formatters.DISCORD)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Tasks listed above.")

    @task.sub_command(name="spend", description=phrases_cmd.get("cmd_task_spend_desc", "Mark time spent on a task"))
    async def task_spend(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int = commands.Param(description=phrases_cmd.get("param_task_id", "Task ID")),
        minutes: int = commands.Param(description=phrases_cmd.get("param_spend_minutes", "Minutes spent")),
    ):
        await inter.response.defer(ephemeral=True)
        if minutes <= 0:
            return await inter.edit_original_response("Error: minutes must be > 0.")
        try:
            is_completed, remaining = provider.spend_task_time(inter.author.id, task_id, minutes)
            if is_completed:
                await inter.edit_original_response(
                    f"Subtracted {minutes} min. Task fully completed and moved to history!"
                )
            else:
                await inter.edit_original_response(f"Subtracted {minutes} min. Remaining duration: {remaining} min.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @task.sub_command(
        name="edit", description=phrases_cmd.get("cmd_task_edit_desc", "Edit specific fields of an existing task")
    )
    async def task_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int = commands.Param(description=phrases_cmd.get("param_task_id", "Task ID")),
        name: str = commands.Param(default=None, description=phrases_cmd.get("param_name", "Name")),
        duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_duration_min", "Duration (min)")
        ),
        description: str = commands.Param(
            default=None, description=phrases_cmd.get("param_description", "Description")
        ),
        priority: int = commands.Param(default=None, description=phrases_cmd.get("param_priority", "Priority")),
        max_chunk_duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_max_chunk", "Max session (min)")
        ),
        break_duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_break", "Break between sessions (min)")
        ),
        min_chunk_duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_min_chunk", "Min chunk (min)")
        ),
        deadline: str = commands.Param(
            default=None, description=phrases_cmd.get("param_deadline", "Deadline (DD.MM.YYYY HH:MM)")
        ),
        depends_on: str = commands.Param(default=None, description=phrases_cmd.get("param_depends", "Dependencies")),
    ):
        await inter.response.defer(ephemeral=True)
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
                user_id=inter.author.id,
                self_id=task_id,
            )

            success = provider.edit_task(inter.author.id, task_id, **updates)
            if success:
                await inter.edit_original_response(f"Task {task_id} updated successfully.")
            else:
                await inter.edit_original_response(f"Task {task_id} not found.")
        except ScheduleValidationError as e:
            await inter.edit_original_response(str(e))
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @task.sub_command(
        name="info", description=phrases_cmd.get("cmd_task_info_desc", "View detailed information about a task")
    )
    async def task_info(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int = commands.Param(description=phrases_cmd.get("param_task_id", "Task ID")),
    ):
        await inter.response.defer(ephemeral=True)
        task = provider.get_task(inter.author.id, task_id)
        if not task:
            await inter.edit_original_response(f"Task {task_id} not found.")
            return

        formatted = schd_item_formatters.format_task_info(task, schd_item_formatters.DISCORD)
        await inter.edit_original_response(formatted)

    @task.sub_command(name="history", description=phrases_cmd.get("cmd_task_history_desc", "List completed tasks"))
    async def task_history(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = provider.list_completed_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No completed tasks found in history.")
            return

        formatted = schd_item_formatters.format_completed_task_list(tasks, schd_item_formatters.DISCORD)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("History listed above.")

    @task.sub_command(
        name="clear_history",
        description=phrases_cmd.get("cmd_task_clear_history_desc", "Clear all completed tasks from history"),
    )
    async def task_clear_history(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        count = provider.clear_completed_tasks(inter.author.id)
        if count > 0:
            await inter.edit_original_response(f"Successfully cleared {count} completed tasks from history.")
        else:
            await inter.edit_original_response("Your task history is already empty.")

    @commands.slash_command(
        test_guilds=settings.guilds, description=phrases_cmd.get("cmd_timeblock_desc", "Manage time blocks")
    )
    async def timeblock(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @timeblock.sub_command(name="add", description=phrases_cmd.get("cmd_timeblock_add_desc", "Add a new time block"))
    async def timeblock_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        start_time: str = commands.Param(description=phrases_cmd.get("param_start_time", "Start time (HH:MM)")),
        end_time: str = commands.Param(description=phrases_cmd.get("param_end_time", "End time (HH:MM)")),
        repeat: str = commands.Param(
            default=REPEAT_ONCE,
            choices=list(TIMEBLOCK_REPEATS),
            description=phrases_cmd.get("param_repeat_block", "Repeat (once/daily/weekly)"),
        ),
        weekdays: str = commands.Param(
            default=None, description=phrases_cmd.get("param_weekdays", "Weekdays (0=Mon..6=Sun, e.g. 0,2,4)")
        ),
        name: str = commands.Param(default="", description=phrases_cmd.get("param_timeblock_name", "Name")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            blocks = provider.list_time_blocks(inter.author.id)
            max_blocks = getattr(settings, "schedule_max_timeblocks_per_user", 20)
            if len(blocks) >= max_blocks:
                return await inter.edit_original_response(
                    f"You have reached the maximum limit of {max_blocks} timeblocks."
                )

            wd_list = None
            if weekdays:
                # Naming days is what makes a block weekly, the same way it does
                # for a routine above -- the choice need not be stated twice.
                repeat = REPEAT_WEEKLY
                wd_list = [int(x.strip()) for x in weekdays.split(",") if x.strip().isdigit()]

            block = validate_timeblock_creation_data(start_time, end_time, repeat, name, wd_list)
            provider.add_time_block(inter.author.id, block)
            await inter.edit_original_response(f"Timeblock added: {start_time} to {end_time}.")
            self.bot.dispatch("schedule_update", inter.channel.id)
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @timeblock.sub_command(
        name="edit",
        description=phrases_cmd.get("cmd_timeblock_edit_desc", "Edit specific fields of an existing time block"),
    )
    async def timeblock_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        block_id: int = commands.Param(description=phrases_cmd.get("param_timeblock_id", "ID of the timeblock")),
        start_time: str = commands.Param(
            default=None, description=phrases_cmd.get("param_start_time", "Start time (HH:MM)")
        ),
        end_time: str = commands.Param(default=None, description=phrases_cmd.get("param_end_time", "End time (HH:MM)")),
        repeat: str = commands.Param(
            default=None,
            choices=list(TIMEBLOCK_REPEATS),
            description=phrases_cmd.get("param_repeat_block", "Repeat (once/daily/weekly)"),
        ),
        weekdays: str = commands.Param(
            default=None, description=phrases_cmd.get("param_weekdays", "Weekdays (0=Mon..6=Sun, e.g. 0,2,4)")
        ),
        name: str = commands.Param(default=None, description=phrases_cmd.get("param_timeblock_name", "Name")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            block = provider.get_time_block(inter.author.id, block_id)
            if not block:
                return await inter.edit_original_response(f"Invalid timeblock ID: {block_id}.")

            wd_list = None
            if weekdays:
                wd_list = [int(x.strip()) for x in weekdays.split(",") if x.strip().isdigit()]

            updates = validate_timeblock_update_data(block, start_time, end_time, repeat, name, wd_list)
            if not updates:
                return await inter.edit_original_response("Nothing to change: name at least one field to edit.")

            provider.edit_time_block(inter.author.id, block_id, **updates)
            await inter.edit_original_response(f"Timeblock {block_id} updated.")
            self.bot.dispatch("schedule_update", inter.channel.id)
        except ScheduleValidationError as e:
            await inter.edit_original_response(str(e))
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @timeblock.sub_command(
        name="remove", description=phrases_cmd.get("cmd_timeblock_remove_desc", "Remove a time block by ID")
    )
    async def timeblock_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        block_id: int = commands.Param(description=phrases_cmd.get("param_timeblock_id", "ID of the timeblock")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            success = provider.remove_time_block(inter.author.id, block_id)
            if success:
                await inter.edit_original_response(f"Timeblock {block_id} removed.")
                self.bot.dispatch("schedule_update", inter.channel.id)
            else:
                await inter.edit_original_response(f"Invalid timeblock ID: {block_id}.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @timeblock.sub_command(name="list", description=phrases_cmd.get("cmd_timeblock_list_desc", "List all time blocks"))
    async def timeblock_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        blocks = provider.list_time_blocks(inter.author.id)
        if not blocks:
            await inter.edit_original_response("No time blocks found.")
            return

        formatted = schd_item_formatters.format_timeblock_list(blocks, schd_item_formatters.DISCORD)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Time blocks listed above.")

    @commands.slash_command(
        test_guilds=settings.guilds, description=phrases_cmd.get("cmd_routine_desc", "Manage routines")
    )
    async def routine(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @routine.sub_command(
        name="add_fixed",
        description=phrases_cmd.get("cmd_routine_add_fixed_desc", "Add a routine that runs at a specific time"),
    )
    async def routine_add_fixed(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(description=phrases_cmd.get("param_name", "Name")),
        time: str = commands.Param(description=phrases_cmd.get("param_time", "Time (HH:MM)")),
        duration_min: int = commands.Param(description=phrases_cmd.get("param_duration_min", "Duration (min)")),
        priority: int = commands.Param(
            default=getattr(settings, "schedule_default_priority", 1),
            description=phrases_cmd.get("param_priority", "Priority"),
        ),
        break_duration_min: int = commands.Param(
            default=getattr(settings, "schedule_default_break_min", 15),
            description=phrases_cmd.get("param_break", "Break (min)"),
        ),
        weekdays: str = commands.Param(
            default=None, description=phrases_cmd.get("param_weekdays", "Weekdays (0=Mon..6=Sun, e.g. 0,2,4)")
        ),
        depends_on: str = commands.Param(default=None, description=phrases_cmd.get("param_depends", "Dependencies")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            routines = provider.list_routines(inter.author.id)
            max_routines = getattr(settings, "schedule_max_routines_per_user", 30)
            if len(routines) >= max_routines:
                return await inter.edit_original_response(
                    f"You have reached the maximum limit of {max_routines} routines."
                )

            wd_list = None
            repeat = "daily"
            if weekdays:
                repeat = "weekly"
                wd_list = [int(x.strip()) for x in weekdays.split(",") if x.strip().isdigit()]

            r = validate_routine_creation_data(
                name=name,
                routine_type="fixed",
                repeat=repeat,
                duration_min=duration_min,
                time_str=time,
                weekdays=wd_list,
                priority=priority,
                break_duration_min=break_duration_min,
                depends_on=depends_on,
                user_id=inter.author.id,
            )
            provider.add_routine(inter.author.id, r)
            await inter.edit_original_response(f"Fixed routine '{name}' added successfully.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @routine.sub_command(
        name="skip",
        description=phrases_cmd.get(
            "cmd_routine_skip_desc", "Skip a routine for today, X days, or until a specific date"
        ),
    )
    async def routine_skip(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int = commands.Param(description=phrases_cmd.get("param_routine_id", "Routine ID")),
        days: int = commands.Param(default=None, description=phrases_cmd.get("param_skip_days", "Days to skip")),
        resume_after: str = commands.Param(
            default=None, description=phrases_cmd.get("param_resume_after", "Resume date (DD.MM.YYYY)")
        ),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            resume_date = validate_skip_routine_data(days=days, resume_after=resume_after)
            success = provider.skip_routine(inter.author.id, routine_id, resume_date)
            if success:
                await inter.edit_original_response(
                    f"Routine {routine_id} skipped. resume_after date set to {resume_date.strftime('%d.%m.%Y')}."
                )
            else:
                await inter.edit_original_response(f"Routine {routine_id} not found.")
        except ScheduleValidationError as e:
            await inter.edit_original_response(f"Error: {e!s}")
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @routine.sub_command(
        name="add_flexible",
        description=phrases_cmd.get(
            "cmd_routine_add_flexible_desc", "Add a routine with a flexible time until a deadline"
        ),
    )
    async def routine_add_flexible(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(description=phrases_cmd.get("param_name", "Name")),
        deadline_time: str = commands.Param(
            description=phrases_cmd.get("param_deadline_time", "Deadline time (HH:MM)")
        ),
        duration_min: int = commands.Param(description=phrases_cmd.get("param_duration_min", "Duration (min)")),
        priority: int = commands.Param(
            default=getattr(settings, "schedule_default_priority", 1),
            description=phrases_cmd.get("param_priority", "Priority"),
        ),
        break_duration_min: int = commands.Param(
            default=getattr(settings, "schedule_default_break_min", 15),
            description=phrases_cmd.get("param_break", "Break (min)"),
        ),
        weekdays: str = commands.Param(
            default=None, description=phrases_cmd.get("param_weekdays", "Weekdays (0=Mon..6=Sun, e.g. 0,2,4)")
        ),
        depends_on: str = commands.Param(default=None, description=phrases_cmd.get("param_depends", "Dependencies")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            routines = provider.list_routines(inter.author.id)
            max_routines = getattr(settings, "schedule_max_routines_per_user", 30)
            if len(routines) >= max_routines:
                return await inter.edit_original_response(
                    f"You have reached the maximum limit of {max_routines} routines."
                )

            wd_list = None
            repeat = "daily"
            if weekdays:
                repeat = "weekly"
                wd_list = [int(x.strip()) for x in weekdays.split(",") if x.strip().isdigit()]

            r = validate_routine_creation_data(
                name=name,
                routine_type="flexible",
                repeat=repeat,
                duration_min=duration_min,
                deadline_time_str=deadline_time,
                weekdays=wd_list,
                priority=priority,
                break_duration_min=break_duration_min,
                depends_on=depends_on,
                user_id=inter.author.id,
            )
            provider.add_routine(inter.author.id, r)
            await inter.edit_original_response(f"Flexible routine '{name}' added successfully.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @routine.sub_command(
        name="info", description=phrases_cmd.get("cmd_routine_info_desc", "View detailed information about a routine")
    )
    async def routine_info(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int = commands.Param(description=phrases_cmd.get("param_routine_id", "Routine ID")),
    ):
        await inter.response.defer(ephemeral=True)
        routine = provider.get_routine(inter.author.id, routine_id)
        if not routine:
            await inter.edit_original_response(f"Routine {routine_id} not found.")
            return

        formatted = schd_item_formatters.format_routine_info(routine, schd_item_formatters.DISCORD)
        await inter.edit_original_response(formatted)

    @routine.sub_command(name="list", description=phrases_cmd.get("cmd_routine_list_desc", "List all routines"))
    async def routine_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        routines = provider.list_routines(inter.author.id)
        if not routines:
            return await inter.edit_original_response("No routines found.")

        formatted = schd_item_formatters.format_routine_list(routines, schd_item_formatters.DISCORD)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Routines listed above.")

    @routine.sub_command(
        name="remove", description=phrases_cmd.get("cmd_routine_remove_desc", "Remove a routine by ID")
    )
    async def routine_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int = commands.Param(description=phrases_cmd.get("param_routine_id", "Routine ID")),
    ):
        await inter.response.defer(ephemeral=True)
        removed = provider.remove_routine(inter.author.id, routine_id)
        if removed:
            await inter.edit_original_response(f"Routine {routine_id} removed successfully.")
        else:
            await inter.edit_original_response(f"Routine {routine_id} not found.")

    @routine.sub_command(
        name="edit", description=phrases_cmd.get("cmd_routine_edit_desc", "Edit specific fields of an existing routine")
    )
    async def routine_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int = commands.Param(description=phrases_cmd.get("param_routine_id", "Routine ID")),
        name: str = commands.Param(default=None, description=phrases_cmd.get("param_name", "Name")),
        routine_type: str = commands.Param(
            default=None, choices=["fixed", "flexible"], description=phrases_cmd.get("param_routine_type", "Type")
        ),
        repeat: str = commands.Param(
            default=None, choices=["daily", "weekly"], description=phrases_cmd.get("param_repeat", "Repeat")
        ),
        duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_duration_min", "Duration (min)")
        ),
        time: str = commands.Param(default=None, description=phrases_cmd.get("param_time", "Time (HH:MM)")),
        deadline_time: str = commands.Param(
            default=None, description=phrases_cmd.get("param_deadline_time", "Deadline time (HH:MM)")
        ),
        weekdays: str = commands.Param(
            default=None, description=phrases_cmd.get("param_weekdays", "Weekdays (0=Mon..6=Sun, e.g. 0,2,4)")
        ),
        priority: int = commands.Param(default=None, description=phrases_cmd.get("param_priority", "Priority")),
        break_duration_min: int = commands.Param(
            default=None, description=phrases_cmd.get("param_break", "Break between sessions (min)")
        ),
        depends_on: str = commands.Param(default=None, description=phrases_cmd.get("param_depends", "Dependencies")),
    ):
        await inter.response.defer(ephemeral=True)
        try:
            wd_list = None
            if weekdays:
                wd_list = [int(x.strip()) for x in weekdays.split(",") if x.strip().isdigit()]

            updates = validate_routine_update_data(
                name=name,
                routine_type=routine_type,
                repeat=repeat,
                duration_min=duration_min,
                time_str=time,
                deadline_time_str=deadline_time,
                weekdays=wd_list,
                priority=priority,
                break_duration_min=break_duration_min,
                depends_on=depends_on,
                user_id=inter.author.id,
                self_id=routine_id,
            )

            success = provider.edit_routine(inter.author.id, routine_id, **updates)
            if success:
                await inter.edit_original_response(f"Routine {routine_id} updated successfully.")
            else:
                await inter.edit_original_response(f"Routine {routine_id} not found.")
        except ScheduleValidationError as e:
            await inter.edit_original_response(str(e))
        except Exception as e:
            await inter.edit_original_response(f"Error: {e!s}")

    @commands.slash_command(
        name="schedule_channel",
        description=phrases_cmd.get("cmd_schedule_channel_desc", "Manage personal schedule channels"),
        test_guilds=settings.guilds,
    )
    async def schedule_channel(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @schedule_channel.sub_command(
        name="create",
        description=phrases_cmd.get("cmd_schedule_channel_create_desc", "Create a personal schedule channels"),
    )
    async def schedule_channel_create(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = utils.get_phrases(inter.guild.id if inter.guild else None).get("schedule", {})

        try:
            schedule_channel, tasks_channel = await create_channel_pair(
                inter,
                registry=provider.channels,
                categories=getattr(settings, "schedule_categories", {}),
                max_per_guild=getattr(settings, "schedule_max_channels_per_guild", 3),
                display_name=f"schedule-{inter.author.display_name}",
                management_name=f"tasks-{inter.author.display_name}",
                reason="Automatic creation of schedule channels",
            )
        except ChannelSetupError as e:
            await inter.edit_original_response(e.text(phrases))
            return

        cache.tasks_channels[tasks_channel.id] = inter.author.id

        # Initialize channel in the loop via event dispatch
        self.bot.dispatch("schedule_init", schedule_channel, inter.author.id)

        msg_created = phrases.get(
            "channel_created",
            "Channels successfully created:\n- Schedule {schedule_channel}\n- Tasks {tasks_channel}",
        ).format(schedule_channel=schedule_channel.mention, tasks_channel=tasks_channel.mention)
        await inter.edit_original_response(msg_created)

        warning_msg = phrases.get(
            "privacy_warning",
            "{user_mention}, please note: view commands (such as `/task list` or `/routine list`) are not ephemeral (private). Their results will be visible to all participants in the channel where you use them. If privacy is important to you, use them only in this private channel.",
        ).format(user_mention=f"<@{inter.author.id}>")
        await tasks_channel.send(warning_msg)

    @schedule_channel.sub_command(
        name="delete",
        description=phrases_cmd.get("cmd_schedule_channel_delete_desc", "Delete your schedule channels"),
    )
    async def schedule_channel_delete(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        phrases = utils.get_phrases(inter.guild.id if inter.guild else None).get("schedule", {})

        removed = provider.channels.remove(inter.author.id)
        if not removed:
            await inter.edit_original_response(phrases.get("no_channels", "You do not have schedule channels."))
            return

        schedule_channel_id = provider.channels.display_channel_id(removed)
        tasks_channel_id = removed.get("tasks_channel_id")

        cache.channel_states.pop(schedule_channel_id, None)
        cache.tasks_channels.pop(tasks_channel_id, None)

        # Tasks, routines and time blocks stay on disk; only the channels go.
        await inter.edit_original_response(
            phrases.get("channels_deleted", "Your schedule channels are being deleted. Your tasks are kept.")
        )

        for channel_id in (schedule_channel_id, tasks_channel_id):
            if not channel_id:
                continue
            try:
                channel = await self.bot.get_or_fetch_channel(channel_id)
                if channel:
                    await channel.delete(reason="Schedule channels removed by their owner")
            except Exception as e:
                logger.warning(f"Could not delete schedule channel {channel_id}: {e}")

    @schedule_channel.sub_command(
        name="settings",
        description=phrases_cmd.get("cmd_schedule_channel_settings_desc", "Set personal schedule configuration"),
    )
    async def schedule_channel_settings(
        self,
        inter: disnake.ApplicationCommandInteraction,
        planning_days: int = commands.Param(
            default=None, description=phrases_cmd.get("param_planning_days", "Planning horizon (days)")
        ),
        priority_threshold: int = commands.Param(
            default=None, description=phrases_cmd.get("param_priority_threshold", "Priority threshold")
        ),
        packer_timeout: float = commands.Param(
            default=None, description=phrases_cmd.get("param_packer_timeout", "Packer timeout")
        ),
        gravity_timeout: float = commands.Param(
            default=None, description=phrases_cmd.get("param_gravity_timeout", "Gravity timeout")
        ),
        step_minutes: int = commands.Param(
            default=None,
            description=phrases_cmd.get(
                "param_step_minutes", "Time step in minutes (higher values increase probability of errors/inaccuracy)"
            ),
            choices=getattr(settings, "schedule_allowed_step_minutes", [1, 5, 15]),
        ),
    ):
        await inter.response.defer(ephemeral=True)

        max_days = getattr(settings, "schedule_max_planning_days", 60)
        max_timeout = getattr(settings, "schedule_max_compute_timeout", 15.0)
        allowed_steps = getattr(settings, "schedule_allowed_step_minutes", [1, 5, 15])

        if planning_days is not None and (planning_days < 1 or planning_days > max_days):
            return await inter.edit_original_response(
                f"Please choose a number of days between 1 and {max_days} (large horizons may cause calculation timeouts)."
            )

        if priority_threshold is not None and (priority_threshold < 0 or priority_threshold > 10):
            return await inter.edit_original_response("Priority threshold must be between 0 and 10.")

        if packer_timeout is not None and (packer_timeout <= 0 or packer_timeout > max_timeout):
            return await inter.edit_original_response(
                f"Packer timeout must be greater than 0 and up to {max_timeout} seconds."
            )

        if gravity_timeout is not None and (gravity_timeout <= 0 or gravity_timeout > max_timeout):
            return await inter.edit_original_response(
                f"Gravity timeout must be greater than 0 and up to {max_timeout} seconds."
            )

        if step_minutes is not None and step_minutes not in allowed_steps:
            return await inter.edit_original_response(f"Step minutes must be one of {allowed_steps}.")

        if (
            planning_days is None
            and priority_threshold is None
            and packer_timeout is None
            and gravity_timeout is None
            and step_minutes is None
        ):
            return await inter.edit_original_response("Please provide at least one setting to update.")

        success = provider.update_schedule_settings(
            inter.author.id,
            planning_days=planning_days,
            priority_threshold=priority_threshold,
            packer_timeout=packer_timeout,
            gravity_timeout=gravity_timeout,
            step_minutes=step_minutes,
        )
        if success:
            msg = "Schedule settings updated:\n"
            if planning_days is not None:
                msg += f"- Planning horizon: {planning_days} days\n"
            if priority_threshold is not None:
                msg += f"- Priority threshold: {priority_threshold}\n"
            if packer_timeout is not None:
                msg += f"- Packer timeout: {packer_timeout}s\n"
            if gravity_timeout is not None:
                msg += f"- Gravity timeout: {gravity_timeout}s\n"
            if step_minutes is not None:
                msg += f"- Time step: {step_minutes} min\n"
            await inter.edit_original_response(msg)

            # The command is used in the tasks channel; the message lives in the
            # schedule one, so the update has to be aimed there.
            entry = provider.channels.get(inter.author.id)
            schedule_channel_id = provider.channels.display_channel_id(entry) if entry else None
            if schedule_channel_id:
                self.bot.dispatch("schedule_update", schedule_channel_id)
        else:
            await inter.edit_original_response(
                "You don't have a schedule channel yet. Please use `/schedule_channel create` first."
            )


def setup(bot):
    bot.add_cog(AutoSchedule(bot))
