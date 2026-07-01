import disnake
from disnake.ext import commands
import datetime

import settings
import core.utils as utils
import core.cache as cache
from core.time_utils import tz

from modules.schedule_models import Task, TimeBlock
from modules.schedule_provider import ScheduleProvider

# We can instantiate the provider here.
provider = ScheduleProvider()


def hhmm_to_datetime(start_hhmm: str, end_hhmm: str):
    now = datetime.datetime.now(tz)
    try:
        sh, sm = map(int, start_hhmm.split(":"))
        eh, em = map(int, end_hhmm.split(":"))
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            raise ValueError()
    except Exception:
        raise ValueError("Invalid time format. Use HH:MM, e.g. 09:00 or 14:30")

    start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)

    if end_dt <= start_dt:
        end_dt += datetime.timedelta(days=1)

    return start_dt, end_dt


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
        priority: int = 1,
        max_chunk_duration_min: int = 45,
        break_duration_min: int = 15,
        min_chunk_duration_min: int = None,
        deadline: str = None,
    ):
        await inter.response.defer(ephemeral=True)
        try:
            deadline_dt = None
            if deadline:
                try:
                    dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                    deadline_dt = dt.replace(tzinfo=tz)
                except ValueError:
                    await inter.edit_original_response("Invalid deadline format. Use 'DD.MM.YYYY HH:MM'")
                    return

            if min_chunk_duration_min and min_chunk_duration_min > 0:
                min_chunk = datetime.timedelta(minutes=min_chunk_duration_min)
            elif max_chunk_duration_min > 0 and duration_min > max_chunk_duration_min:
                min_chunk = datetime.timedelta(minutes=min(15, max_chunk_duration_min))
            else:
                min_chunk = None

            max_chunk = datetime.timedelta(minutes=max_chunk_duration_min) if max_chunk_duration_min > 0 else None

            new_task = Task(
                id=0,
                name=name,
                duration=datetime.timedelta(minutes=duration_min),
                description=description,
                deadline=deadline_dt,
                priority=priority,
                max_chunk_duration=max_chunk,
                break_duration=datetime.timedelta(minutes=break_duration_min),
                min_chunk_duration=min_chunk,
            )

            new_id = provider.add_task(inter.author.id, new_task)
            await inter.edit_original_response(f"Task '{name}' added successfully with ID {new_id}.")
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

        lines = ["**Your Tasks:**"]
        for t in tasks:
            dur_mins = int(t.duration.total_seconds() // 60)
            lines.append(f"`[ID: {t.id}]` **{t.name}** - {dur_mins} min (Priority: {t.priority})")

        await utils.send_long_message(inter.channel, "\n".join(lines))
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
    ):
        await inter.response.defer(ephemeral=True)
        try:
            updates = {}

            if name is not None:
                updates["name"] = name.replace("\t", " ").replace("\n", " ").strip()
            if duration_min is not None:
                updates["duration"] = datetime.timedelta(minutes=duration_min)
            if description is not None:
                updates["description"] = description.replace("\t", " ").replace("\n", " ").strip()
            if priority is not None:
                updates["priority"] = priority
            if max_chunk_duration_min is not None:
                updates["max_chunk_duration"] = datetime.timedelta(minutes=max_chunk_duration_min)
            if break_duration_min is not None:
                updates["break_duration"] = datetime.timedelta(minutes=break_duration_min)
            if min_chunk_duration_min is not None:
                if min_chunk_duration_min > 0:
                    updates["min_chunk_duration"] = datetime.timedelta(minutes=min_chunk_duration_min)
                else:
                    if max_chunk_duration_min is not None and max_chunk_duration_min > 0:
                        updates["min_chunk_duration"] = datetime.timedelta(minutes=min(15, max_chunk_duration_min))
                    else:
                        updates["min_chunk_duration"] = None

            if deadline is not None:
                if deadline.lower() == "none":
                    updates["deadline"] = None
                else:
                    dt = datetime.datetime.strptime(deadline, "%d.%m.%Y %H:%M")
                    updates["deadline"] = dt.replace(tzinfo=tz)

            success = provider.edit_task(inter.author.id, task_id, **updates)
            if success:
                await inter.edit_original_response(f"Task {task_id} updated successfully.")
            else:
                await inter.edit_original_response(f"Task {task_id} not found.")
        except ValueError:
            await inter.edit_original_response("Invalid deadline format. Use 'DD.MM.YYYY HH:MM' or 'none'.")
        except Exception as e:
            await inter.edit_original_response(f"Error: {str(e)}")

    @task.sub_command(name="info", description="View detailed information about a task")
    async def task_info(self, inter: disnake.ApplicationCommandInteraction, task_id: int):
        await inter.response.defer(ephemeral=True)
        task = provider.get_task(inter.author.id, task_id)
        if not task:
            await inter.edit_original_response(f"Task {task_id} not found.")
            return

        lines = [
            f"**Task Details (ID: {task.id})**",
            f"**Name:** {task.name}",
            f"**Description:** {task.description if task.description.strip() else '(none)'}",
            f"**Priority:** {task.priority}",
            f"**Total Duration:** {int(task.duration.total_seconds() // 60)} min",
            f"**Session:** {int(task.max_chunk_duration.total_seconds() // 60) if task.max_chunk_duration else 'N/A'} min  |  **Break:** {int(task.break_duration.total_seconds() // 60)} min",
        ]

        if task.deadline:
            dl_str = task.deadline.strftime("%d.%m.%Y %H:%M")
            lines.insert(3, f"**Deadline:** {dl_str}")
        else:
            lines.insert(3, "**Deadline:** none")

        if task.min_chunk_duration:
            lines.append(
                f"**Min session shortening allowed:** {int(task.min_chunk_duration.total_seconds() // 60)} min"
            )

        await inter.edit_original_response("\n".join(lines))

    @task.sub_command(name="history", description="List completed tasks")
    async def task_history(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        tasks = provider.list_completed_tasks(inter.author.id)
        if not tasks:
            await inter.edit_original_response("No completed tasks found in history.")
            return

        lines = ["**Completed Tasks:**"]
        for t in tasks:
            lines.append(f"`[ID: {t.id}]` **{t.name}** (Priority: {t.priority})")

        await utils.send_long_message(inter.channel, "\n".join(lines))
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
            start_dt, end_dt = hhmm_to_datetime(start_time, end_time)
            block = TimeBlock(
                start=start_dt,
                end=end_dt,
                daily=daily,
            )
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

        lines = ["**Your Time Blocks:**"]
        for i, b in enumerate(blocks):
            try:
                st = b.start.strftime("%H:%M") if hasattr(b.start, "strftime") else "???"
                et = b.end.strftime("%H:%M") if hasattr(b.end, "strftime") else "???"
                rep = "Daily" if getattr(b, "daily", False) else "One-time"
                lines.append(f"`[{i + 1}]` {st} - {et} ({rep})")
            except Exception:
                lines.append(f"`[{i + 1}]` Invalid Block Data")

        await utils.send_long_message(inter.channel, "\n".join(lines))
        await inter.edit_original_response("Time blocks listed above.")

    @commands.slash_command(test_guilds=settings.guilds)
    @commands.is_owner()
    async def routine(self, inter: disnake.ApplicationCommandInteraction):
        pass

    @routine.sub_command(name="add", description="Add a routine")
    async def routine_add(self, inter: disnake.ApplicationCommandInteraction):
        await inter.response.defer(ephemeral=True)
        try:
            provider.add_routine(inter.author.id, {"dummy": "data"})
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


def setup(bot):
    bot.add_cog(AutoSchedule(bot))
