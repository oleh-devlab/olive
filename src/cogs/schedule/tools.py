import disnake
from disnake.ext import commands

import settings
import core.utils as utils
import core.cache as cache
from modules.schedule_provider import ScheduleProvider
from modules.schedule_exceptions import ScheduleValidationError
import modules.schd_item_formatters as schd_item_formatters
from modules.schedule_validators import (
    validate_task_creation_data,
    validate_task_update_data,
    validate_routine_creation_data,
    validate_timeblock_creation_data,
    validate_routine_update_data,
    validate_skip_routine_data,
)

# We can instantiate the provider here.
provider = ScheduleProvider()


class AutoSchedule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def task(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @task.sub_command(name="add", description="Add a new task")
    async def task_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str,
        duration_min: int,
        description: str = "",
        priority: int = getattr(settings, "schedule_default_priority", 1),
        max_chunk_duration_min: int = getattr(settings, "schedule_default_max_chunk_min", 45),
        break_duration_min: int = getattr(settings, "schedule_default_break_min", 15),
        min_chunk_duration_min: int = None,
        deadline: str = None,
        depends_on: str = None,
    ):
        await inter.response.defer(ephemeral=True)
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
                user_id=inter.author.id,
            )
            new_id = provider.add_task(inter.author.id, new_task)
            await inter.edit_original_response(f"Task '{name}' added successfully with ID {new_id}.")
        except ScheduleValidationError as e:
            await inter.edit_original_response(str(e))
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="remove", description="Remove a task by ID")
    async def task_remove(self, inter: disnake.ApplicationCommandInteraction, task_id: int):
        await inter.response.defer(ephemeral=True)
        removed = provider.remove_task(inter.author.id, task_id)
        if removed:
            await inter.edit_original_response(f"Task {task_id} removed successfully.")
        else:
            await inter.edit_original_response(f"Task {task_id} not found.")

    @task.sub_command(name="list", description="List all current tasks")
    async def task_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = provider.list_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No tasks found.")
            return

        formatted = schd_item_formatters.format_task_list(tasks, use_markdown=True)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Tasks listed above.")

    @task.sub_command(name="spend", description="Mark time spent on a task")
    async def task_spend(self, inter: disnake.ApplicationCommandInteraction, task_id: int, minutes: int):
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
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="edit", description="Edit specific fields of an existing task")
    async def task_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        task_id: int,
        name: str = None,
        duration_min: int = None,
        description: str = None,
        priority: int = None,
        max_chunk_duration_min: int = None,
        break_duration_min: int = None,
        min_chunk_duration_min: int = None,
        deadline: str = None,
        depends_on: str = None,
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
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="info", description="View detailed information about a task")
    async def task_info(self, inter: disnake.ApplicationCommandInteraction, task_id: int):
        await inter.response.defer(ephemeral=True)
        task = provider.get_task(inter.author.id, task_id)
        if not task:
            await inter.edit_original_response(f"Task {task_id} not found.")
            return

        formatted = schd_item_formatters.format_task_info(task, use_markdown=True)
        await inter.edit_original_response(formatted)

    @task.sub_command(name="history", description="List completed tasks")
    async def task_history(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = provider.list_completed_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No completed tasks found in history.")
            return

        formatted = schd_item_formatters.format_completed_task_list(tasks, use_markdown=True)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("History listed above.")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def timeblock(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @timeblock.sub_command(name="add", description="Add a new time block")
    async def timeblock_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        start_time: str,
        end_time: str,
        daily: bool = True,
    ):
        await inter.response.defer(ephemeral=True)
        try:
            block = validate_timeblock_creation_data(start_time, end_time, daily)
            provider.add_time_block(inter.author.id, block)
            await inter.edit_original_response(f"Timeblock added: {start_time} to {end_time}.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @timeblock.sub_command(name="remove", description="Remove a time block by index")
    async def timeblock_remove(self, inter: disnake.ApplicationCommandInteraction, index: int):
        await inter.response.defer(ephemeral=True)
        removed = provider.remove_time_block(inter.author.id, index - 1)
        if removed:
            await inter.edit_original_response(f"Timeblock {index} removed successfully.")
        else:
            await inter.edit_original_response(f"Timeblock {index} not found.")

    @timeblock.sub_command(name="list", description="List all time blocks")
    async def timeblock_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        blocks = provider.list_time_blocks(inter.author.id)
        if not blocks:
            await inter.edit_original_response("No time blocks found.")
            return

        formatted = schd_item_formatters.format_timeblock_list(blocks, use_markdown=True)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Time blocks listed above.")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def routine(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @routine.sub_command(name="add_fixed", description="Add a routine that runs at a specific time")
    async def routine_add_fixed(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str,
        time: str,
        duration_min: int,
        priority: int = getattr(settings, "schedule_default_priority", 1),
        weekdays: str = None,
        depends_on: str = None,
    ):
        await inter.response.defer(ephemeral=True)
        try:
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
                depends_on=depends_on,
                user_id=inter.author.id,
            )
            provider.add_routine(inter.author.id, r)
            await inter.edit_original_response(f"Fixed routine '{name}' added successfully.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @routine.sub_command(name="skip", description="Skip a routine for today, X days, or until a specific date")
    async def routine_skip(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int,
        days: int = None,
        resume_after: str = None,
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
            await inter.edit_original_response(f"Error: {str(e)}")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @routine.sub_command(name="add_flexible", description="Add a routine with a flexible time until a deadline")
    async def routine_add_flexible(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str,
        deadline_time: str,
        duration_min: int,
        priority: int = getattr(settings, "schedule_default_priority", 1),
        weekdays: str = None,
        depends_on: str = None,
    ):
        await inter.response.defer(ephemeral=True)
        try:
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
                depends_on=depends_on,
                user_id=inter.author.id,
            )
            provider.add_routine(inter.author.id, r)
            await inter.edit_original_response(f"Flexible routine '{name}' added successfully.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @routine.sub_command(name="info", description="View detailed information about a routine")
    async def routine_info(self, inter: disnake.ApplicationCommandInteraction, routine_id: int):
        await inter.response.defer(ephemeral=True)
        routine = provider.get_routine(inter.author.id, routine_id)
        if not routine:
            await inter.edit_original_response(f"Routine {routine_id} not found.")
            return

        formatted = schd_item_formatters.format_routine_info(routine, use_markdown=True)
        await inter.edit_original_response(formatted)

    @routine.sub_command(name="list", description="List all routines")
    async def routine_list(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        routines = provider.list_routines(inter.author.id)
        if not routines:
            return await inter.edit_original_response("No routines found.")

        formatted = schd_item_formatters.format_routine_list(routines, use_markdown=True)
        await utils.send_long_message(inter.channel, formatted)
        await inter.edit_original_response("Routines listed above.")

    @routine.sub_command(name="remove", description="Remove a routine by ID")
    async def routine_remove(self, inter: disnake.ApplicationCommandInteraction, routine_id: int):
        await inter.response.defer(ephemeral=True)
        removed = provider.remove_routine(inter.author.id, routine_id)
        if removed:
            await inter.edit_original_response(f"Routine {routine_id} removed successfully.")
        else:
            await inter.edit_original_response(f"Routine {routine_id} not found.")

    @routine.sub_command(name="edit", description="Edit specific fields of an existing routine")
    async def routine_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        routine_id: int,
        name: str = None,
        routine_type: str = commands.Param(default=None, choices=["fixed", "flexible"]),
        repeat: str = commands.Param(default=None, choices=["daily", "weekly"]),
        duration_min: int = None,
        time: str = None,
        deadline_time: str = None,
        weekdays: str = None,
        priority: int = None,
        depends_on: str = None,
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
            await inter.edit_original_response(f"Error: {str(e)}")

    @commands.slash_command(
        name="schedule_channel", description="Manage personal schedule channels", test_guilds=settings.guilds
    )
    async def schedule_channel(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @schedule_channel.sub_command(name="create", description="Create a personal schedule channel")
    async def schedule_channel_create(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)

        schedule_categories = getattr(settings, "schedule_categories", {})
        phrases = utils.get_phrases(inter.guild.id).get("schedule", {})

        if inter.guild.id not in schedule_categories:
            await inter.edit_original_response(phrases.get("not_available_server", "Not available on this server."))
            return

        category_id = schedule_categories[inter.guild.id]
        category = inter.guild.get_channel(category_id)
        if not category:
            await inter.edit_original_response(
                phrases.get("category_not_found", "Category for channels not found. Contact administrator.")
            )
            return

        data = provider.load_channels()
        user_id_str = str(inter.author.id)

        if user_id_str in data:
            await inter.edit_original_response(
                phrases.get("channel_already_exists", "You already have a schedule channel on one of the servers.")
            )
            return

        # Check limit per server
        channels_in_guild = sum(1 for d in data.values() if d.get("guild_id") == inter.guild.id)
        if channels_in_guild >= 3:
            await inter.edit_original_response(
                phrases.get("limit_exceeded", "Schedule channel limit exceeded for this server (max 3).")
            )
            return

        try:
            schedule_overwrites = {
                inter.guild.default_role: disnake.PermissionOverwrite(read_messages=False),
                inter.author: disnake.PermissionOverwrite(read_messages=True, send_messages=False),
                inter.guild.me: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            tasks_overwrites = {
                inter.guild.default_role: disnake.PermissionOverwrite(read_messages=False),
                inter.author: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
                inter.guild.me: disnake.PermissionOverwrite(read_messages=True, send_messages=True),
            }

            schedule_channel = await inter.guild.create_text_channel(
                name=f"schedule-{inter.author.display_name}",
                category=category,
                overwrites=schedule_overwrites,
                reason="Automatic creation of schedule channel",
            )

            tasks_channel = await inter.guild.create_text_channel(
                name=f"tasks-{inter.author.display_name}",
                category=category,
                overwrites=tasks_overwrites,
                reason="Automatic creation of tasks channel",
            )

            data[user_id_str] = {
                "channel_id": schedule_channel.id,
                "tasks_channel_id": tasks_channel.id,
                "guild_id": inter.guild.id,
            }
            provider.save_channels(data)

            if not hasattr(cache, "tasks_channels"):
                cache.tasks_channels = {}
            cache.tasks_channels[tasks_channel.id] = inter.author.id

            # Initialize channel in the loop via event dispatch
            self.bot.dispatch("schedule_init", schedule_channel, inter.author.id)

            msg_created = phrases.get(
                "channel_created", "Channels successfully created: Schedule {schedule_channel}, Tasks {tasks_channel}"
            ).format(schedule_channel=schedule_channel.mention, tasks_channel=tasks_channel.mention)
            await inter.edit_original_response(msg_created)

        except Exception as e:
            print(f"Error creating channel: {e}")
            await inter.edit_original_response(
                phrases.get("creation_error", "An error occurred while creating the channel.")
            )

    @schedule_channel.sub_command(name="settings", description="Set personal schedule configuration")
    async def schedule_channel_settings(
        self,
        inter: disnake.ApplicationCommandInteraction,
        planning_days: int = None,
        priority_threshold: int = None,
        compute_timeout: float = None,
        step_minutes: int = commands.Param(
            default=None,
            description="Time step in minutes (higher values increase probability of errors/inaccuracy)",
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

        if compute_timeout is not None and (compute_timeout <= 0 or compute_timeout > max_timeout):
            return await inter.edit_original_response(
                f"Compute timeout must be greater than 0 and up to {max_timeout} seconds."
            )

        if step_minutes is not None and step_minutes not in allowed_steps:
            return await inter.edit_original_response(f"Step minutes must be one of {allowed_steps}.")

        if planning_days is None and priority_threshold is None and compute_timeout is None and step_minutes is None:
            return await inter.edit_original_response("Please provide at least one setting to update.")

        success = provider.update_schedule_settings(
            inter.author.id,
            planning_days=planning_days,
            priority_threshold=priority_threshold,
            compute_timeout=compute_timeout,
            step_minutes=step_minutes,
        )
        if success:
            msg = "Schedule settings updated:\n"
            if planning_days is not None:
                msg += f"- Planning horizon: {planning_days} days\n"
            if priority_threshold is not None:
                msg += f"- Priority threshold: {priority_threshold}\n"
            if compute_timeout is not None:
                msg += f"- Compute timeout: {compute_timeout}s\n"
            if step_minutes is not None:
                msg += f"- Time step: {step_minutes} min\n"
            await inter.edit_original_response(msg)
            self.bot.dispatch("schedule_update", inter.channel.id)
        else:
            await inter.edit_original_response(
                "You don't have a schedule channel yet. Please use `/schedule_channel create` first."
            )


def setup(bot):
    bot.add_cog(AutoSchedule(bot))
